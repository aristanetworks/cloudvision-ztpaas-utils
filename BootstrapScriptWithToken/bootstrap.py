#!/usr/bin/python
# Copyright (c) 2021 Arista Networks, Inc.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the COPYING file.

import subprocess
import requests
import os
import json
import sys
import Cell
import urlparse


##############  USER INPUT  ##############
# address for CVaaS, usually just "www.arista.io"
cvAddr = ""

# enrollment token to be copied from CVaaS Device Registration page
enrollmentToken = ""

# Add proxy url if device is behind proxy server, leave it as an empty string otherwise
cvproxy = ""

# eosUrl is an optional parameter, which needs to be added only if the EOS version
# is <4.24, in which case, SysDbHelperUtils is not present on the device.
eosUrl = ""


##############  CONSTANTS  ##############
SECURE_HTTPS_PORT = "443"
SECURE_TOKEN = "token-secure"
INGEST_PORT = "9910"
INGEST_TOKEN = "token"
TOKEN_FILE_PATH = "/tmp/token.tok"
BOOT_SCRIPT_PATH = "/tmp/bootstrap-script"
REDIRECTOR_PATH = "api/v3/services/arista.redirector.v1.AssignmentService/GetOne"


##############  HELPER FUNCTIONS  ##############
proxies = { "https" : cvproxy, "http" : cvproxy }

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


def tryImageUpgrade( e ):
   # Raise import error if eosUrl is empty
   if eosUrl == "":
      print( "Specify eosUrl for EOS version upgrade" )
      raise( e )
   subprocess.call( [ "mv", "/mnt/flash/EOS.swi", "/mnt/flash/EOS.swi.bak" ] )
   try:
      cmd = "wget {} -O /mnt/flash/EOS.swi; sudo ip netns exec default /usr/bin/FastCli \
         -p15 -G -A -c $'configure\nboot system flash:/EOS.swi'".format(eosUrl)
      subprocess.check_output( cmd, shell=True, stderr=subprocess.STDOUT )
   except subprocess.CalledProcessError as err:
      # If the link to eosUrl specified is incorrect, then revert back to the older version
      subprocess.call( [ "mv", "/mnt/flash/EOS.swi.bak", "/mnt/flash/EOS.swi" ] )
      print( err.output )
      raise( err )
   subprocess.call( [ "rm", "/mnt/flash/EOS.swi.bak" ] )
   subprocess.call( [ "reboot" ] )


##############  SysdbHelperUtils IMPORT HANDLING  ##############
# SysdbHelperUtils library is not present in most EOS versions <4.24. Thus in such a
# case, we locally upgrade the EOS version and reboot the  device. After the upgrade,
# EOS version of device must be upgraded and import for SysdbHelperUtils will not fail.
try:
   from SysdbHelperUtils import SysdbPathHelper
except ImportError as e:
   tryImageUpgrade( e )


##############  MAIN SCRIPT  ##############
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

      # A timeout of 60 seconds is used with TerminAttr commands since in most
      # versions of TerminAttr, the command execution does not finish if a wrong
      # flag is specified leading to the catch block being never executed
      cmd = "timeout 60s "
      cmd += "/usr/bin/TerminAttr"
      cmd += " -cvauth " + self.tokenType + "," + TOKEN_FILE_PATH
      cmd += " -cvaddr " + self.enrollAddr
      cmd += " -enrollonly"

      # Use cvproxy only when it is specified, this is to ensure that if we are on
      # older version of EOS that doesn't support cvporxy flag, the script won't fail
      if cvproxy != "":
         cmd += " -cvproxy=" + cvproxy

      try:
         subprocess.check_output( cmd, shell=True, stderr=subprocess.STDOUT )
      except subprocess.CalledProcessError as e:
         # If the above subprocess call times out, it means that -cvproxy
         # flag is not present in the Terminattr version running on that device
         # Hence we have to do an image upgrade in this case.
         if e.returncode == 124: # timeout
            tryImageUpgrade( e )
         else:
            print( e.output )
            raise e

      print( "step 1 done, exchanged enrollment token for client certificates" )


##################################################################################
# Step 2: get the path of stored client certificate
##################################################################################
   def getCertificatePaths( self ):
      # Timeout added for TerminAttr
      cmd = "timeout 60s "
      cmd += "/usr/bin/TerminAttr"
      cmd += " -cvaddr " + self.enrollAddr
      cmd += " -certsconfig"

      try:
         response = subprocess.check_output( cmd, shell=True, stderr=subprocess.STDOUT )
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
               cert=( self.certificate, self.key ), proxies=proxies )
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
