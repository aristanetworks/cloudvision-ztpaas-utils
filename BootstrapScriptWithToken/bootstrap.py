#!/usr/bin/python
# Copyright (c) 2021 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the COPYING file.

import subprocess
import requests
import os
import json
import sys
from SysdbHelperUtils import SysdbPathHelper
import Cell
import urlparse


############## USER INPUT #############
cvAddr = ""
enrollmentToken = ""
# Add proxy url if device is behind proxy server
# else leave it as an empty string
cvproxy = ""

############## CONSTANTS ##############
SECURE_HTTPS_PORT = "443"
SECURE_TOKEN = "token-secure"
INGEST_PORT = "9910"
INGEST_TOKEN = "token"
TOKEN_FILE_PATH = "/tmp/token.tok"
BOOT_SCRIPT_PATH = "/tmp/bootstrap-script"
REDIRECTOR_PATH = "api/v3/services/arista.redirector.v1.AssignmentService/GetOne"


########### HELPER FUNCTIONS ##########
# Given a filepath and a key, getValueFromFile searches for key=VALUE in it
# and returns the found value without any whitespaces. In case no key specified,
# gives the first string in the first line of the file.
def getValueFromFile( filename, key ):
   if not key:
      with open( filename, "r" ) as f:
         return f.readline().split()[ 0 ]
   else:
      with open( filename, "r" ) as f:
         lines = f.readlines()
         for line in lines:
            if key in line :
               return line.split( "=" )[ 1 ].rstrip( "\n" )
   return None


########### MAIN SCRIPT ##########
class BootstrapManager( object ):
   def __init__( self ):
      super( BootstrapManager, self ).__init__()

   def getBootstrapURL( self, url ):
      pass

##################################################################################
# step 1: get client certificate using the enrollment token
##################################################################################
   def getClientCertificates( self ):
      with open( TOKEN_FILE_PATH, "w" ) as f:
         f.write( enrollmentToken )

      cmd = "/usr/bin/TerminAttr"
      cmd += " -cvauth " + self.tokenType + "," + TOKEN_FILE_PATH
      cmd += " -cvaddr " + self.enrollAddr
      cmd += " -enrollonly"
      cmd += " -cvproxy=" + cvproxy


      try:
         subprocess.check_output( cmd, shell=True, stderr=subprocess.STDOUT )
      except subprocess.CalledProcessError as e:
         print( e.output )
         raise e
      print( "step 1 done, exchanged enrollment token for client certificates" )

##################################################################################
# Step 2: get the path of stored client certificate
##################################################################################
   def getCertificatePaths( self ):
      cmd = "/usr/bin/TerminAttr"
      cmd += " -cvaddr " + self.enrollAddr
      cmd += " -certsconfig"

      try:
         response = subprocess.check_output( cmd,
               shell=True, stderr=subprocess.STDOUT )
         json_response = json.loads( response )
         self.certificate = str( json_response[ self.enrollAddr ][ 'certFile' ] )
         self.key = str( json_response[ self.enrollAddr ][ 'keyFile' ] )
      except subprocess.CalledProcessError:
         basePath = "/persist/secure/ssl/terminattr/primary/"
         self.certificate = basePath + "certs/client.crt"
         self.key = basePath + "keys/client.key"

      print( "step 2 done, obtained client certificates location from TA" )
      print( "ceriticate location: " + self.certificate )
      print( "key location: " + self.key )

##################################################################################
# step 3 get bootstrap script using the certificates
##################################################################################
   def checkWithRedirector( self, serialNum ):
      if not self.redirectorURL:
         return

      try:
         payload = '{"key": {"system_id": "%s"}}' % serialNum
         response = requests.post( self.redirectorURL.geturl(), data=payload,
               cert=( self.certificate, self.key ) )
         response.raise_for_status()
         clusters = response.json()[ 0 ][ "value" ][ "clusters" ][ "values" ]
         assignment = clusters [ 0 ][ "hosts" ][ "values" ][ 0 ]
         self.bootstrapURL = self.getBootstrapURL( assignment )
      except Exception as e:
         print( "error talking to redirector: %s" % e )
         print( "no assignment found from redirector" )

   def getBootstrapScript( self ):
      # setting Sysdb access variables
      sysname = os.environ.get( "SYSNAME", "ar" )
      pathHelper = SysdbPathHelper( sysname )

      # sysdb paths accessed
      cellID = str( Cell.cellId() )
      mibStatus = pathHelper.getEntity( "hardware/entmib" )
      tpmStatus = pathHelper.getEntity( "cell/" + cellID + "/hardware/tpm/status" )

      # setting header information
      headers = {}
      headers[ 'X-Arista-SystemMAC' ] = mibStatus.systemMacAddr
      headers[ 'X-Arista-ModelName' ] = mibStatus.root.modelName
      headers[ 'X-Arista-HardwareVersion' ] = mibStatus.root.hardwareRev
      headers[ 'X-Arista-Serial' ] = mibStatus.root.serialNum

      headers[ 'X-Arista-TpmApi' ] = tpmStatus.tpmVersion
      headers[ 'X-Arista-TpmFwVersion' ] = tpmStatus.firmwareVersion
      headers[ 'X-Arista-SecureZtp' ] = str( tpmStatus.boardValidated )

      headers[ 'X-Arista-SoftwareVersion' ] = getValueFromFile(
            "/etc/swi-version", "SWI_VERSION" )
      headers[ 'X-Arista-Architecture' ] = getValueFromFile( "/etc/arch", "" )

      # get the URL to the right cluster
      self.checkWithRedirector( mibStatus.root.serialNum )

      # making the request and writing to file
      proxies = { "https" : cvproxy }
      response = requests.get( self.bootstrapURL.geturl(), headers=headers,
            cert=( self.certificate, self.key ), proxies=proxies )
      response.raise_for_status()
      with open( BOOT_SCRIPT_PATH, "w" ) as f:
         f.write( response.text )

      print( "step 3.1 done, bootstrap script fetched and stored on disk" )

   # execute the obtained bootstrap file
   def executeBootstrap( self ):
      cmd = "python " + BOOT_SCRIPT_PATH
      os.environ['CVPROXY'] = cvproxy
      try:
         subprocess.check_output( cmd, shell=True, stderr=subprocess.STDOUT,
                                  env=os.environ )
      except subprocess.CalledProcessError as e:
         print( e.output )
         raise e
      print( "step 3.2 done, executing the fetched bootstrap script" )

   def run( self ):
      self.getClientCertificates()
      self.getCertificatePaths()
      self.getBootstrapScript()
      self.executeBootstrap()


class CloudBootstrapManager( BootstrapManager ):
   def __init__( self ):
      super( CloudBootstrapManager, self ).__init__()

      self.bootstrapURL = self.getBootstrapURL( cvAddr )
      self.redirectorURL = self.bootstrapURL._replace( path=REDIRECTOR_PATH )
      self.tokenType = SECURE_TOKEN
      self.enrollAddr = self.bootstrapURL.netloc + ":" + SECURE_HTTPS_PORT
      self.enrollAddr = self.enrollAddr.replace( "www", "apiserver" )

   def getBootstrapURL( self, addr ):
      addr = addr.replace( "apiserver", "www" )
      addrURL = urlparse.urlparse( addr )
      if addrURL.netloc == "":
         addrURL = addrURL._replace( path="", netloc=addrURL.path )
      if addrURL.path == "":
         addrURL = addrURL._replace( path="/ztp/bootstrap" )
      if addrURL.scheme == "":
         addrURL = addrURL._replace( scheme="https" )
      return addrURL


class OnPremBootstrapManager( BootstrapManager ):
   def __init__( self ):
      super( OnPremBootstrapManager, self ).__init__()

      self.bootstrapURL = self.getBootstrapURL( cvAddr )
      self.redirectorURL = None
      self.tokenType = INGEST_TOKEN
      self.enrollAddr = self.bootstrapURL.netloc + ":" + INGEST_PORT

   def getBootstrapURL( self, addr ):
      addrURL = urlparse.urlparse( addr )
      if addrURL.netloc == "":
         addrURL = addrURL._replace( path="", netloc=addrURL.path )
      if addrURL.path == "":
         addrURL = addrURL._replace( path="/ztp/bootstrap" )
      if addrURL.scheme == "":
         addrURL = addrURL._replace( scheme="http" )
      return addrURL


if __name__ == "__main__":
   # check inputs
   if cvAddr == "":
      sys.exit( "error: address to CVP missing" )
   if enrollmentToken == "":
      sys.exit( "error: enrollment token missing" )

   # check whether it is cloud or on prem
   if cvAddr.find( "arista.io" ) != -1 :
      bm = CloudBootstrapManager()
   else:
      bm = OnPremBootstrapManager()

   # run the script
   bm.run()
