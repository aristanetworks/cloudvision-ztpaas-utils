#!/usr/bin/python
# Copyright (c) 2021 Arista Networks, Inc.  All rights reserved.
# Use of this source code is governed by the Apache License 2.0
# that can be found in the COPYING file.

import os
import subprocess
import sys
import time
import json
import signal

##############  USER INPUT  ##############
# Note: If you are saving the file on windows, please make sure to use linux (LF) as newline.
# By default, windows uses (CR LF), you need to convert the newline char to linux (LF).

# address for CVaaS, usually just "www.arista.io"
cvAddr = ""

# enrollment token to be copied from CVaaS Device Registration page
enrollmentToken = ""

# Add proxy url if device is behind proxy server, leave it as an empty string otherwise
cvproxy = ""

# eosUrl is an optional parameter, which needs to be added only if the EOS version
# is <4.24, in which case, SysDbHelperUtils is not present on the device.
# This needs to be a http URL pointing to a SWI image on the local network.
eosUrl = ""

# ntpServer is an optional parameter. If specified, the script will configure
# the URL supplied in this variable to be used as the NTP server.
# For example:
# ntpServer = "ntp1.aristanetworks.com"
ntpServer = ""


##############  CONSTANTS  ##############
SECURE_HTTPS_PORT = "443"
SECURE_TOKEN = "token-secure"
INGEST_TOKEN = "token"
TOKEN_FILE_PATH = "/tmp/token.tok"
BOOT_SCRIPT_PATH = "/tmp/bootstrap-script"
REDIRECTOR_PATH = "api/v3/services/arista.redirector.v1.AssignmentService/GetOne"


##############  HELPER FUNCTIONS  ##############
proxies = { "https" : cvproxy, "http" : cvproxy }

def monitorNtpSync():
   timeInterval = 10
   expo = 2
   for i in range(5):
      print("Polling NTP status....")
      try:
         ntpStatInfo = subprocess.call(["ntpstat"])
      except:
         raise Exception("ntpstat command failed. Aborting")
      print("NTP synchronization status: "+str(ntpStatInfo))
      if ntpStatInfo == 0:
         print("NTP synchronization complete.")
         return
      time.sleep(timeInterval)
      timeInterval *= expo
   raise Exception("NTP syncronization failed. Timing out....")

# Class is used to execute commands in EOS shell
class CliManager(object):
   FAST_CLI_BINARY = "/usr/bin/FastCli"
   def __init__(self):
      self.fastCliBinary = CliManager.FAST_CLI_BINARY
      self.confidenceCheck()

   def confidenceCheck(self):
      assert os.path.isfile(self.fastCliBinary), "FastCli Binary Not Found"

   def runCommands(self, cmdList):
      cmdOutput = ""
      rc = 0
      errMsg = ""
      try:
         cmds = "\n".join(cmdList)
         cmdOutput = subprocess.check_output(
            "echo -e '" + cmds + "' | " + self.fastCliBinary, shell=True, stderr=subprocess.STDOUT,
            universal_newlines=True )
      except subprocess.CalledProcessError as e:
         rc = e.returncode
         errMsg = e.output
         print("running commands %s errMsg %s" % (cmds, errMsg))
         return (rc, errMsg)

      if cmdOutput:
         for line in cmdOutput.split('\n'):
            if line.startswith('%'):
               errMsg = cmdOutput
               print("running commands %s errMsg %s" % (cmds, errMsg))
               return(1, errMsg)
      return (0, cmdOutput)

# stops and restarts ntp with a specified ntp server
def configureAndRestartNTP(ntpServer):
   cli = CliManager()

   # Command to stop the ntp process
   stopNtp = [ 'en', 'configure', 'no ntp', 'exit' ]
   output, err = cli.runCommands( stopNtp )
   if output != 0:
      print("Trying to run commands [en, configure, no ntp, exit]")
      print("Output: ",str(output))
      print("Error: ",str(err))
      print("NTP server could not be stopped. Aborting")
      raise Exception("NTP server could not be stopped, error:{}. Aborting".format(str(err)))

   # Command to configure and restart ntp process.
   # Note: iburst flag is added for faster synchronization
   configNtp = ['en','configure', 'ntp server {} prefer iburst'.format(ntpServer),'exit']
   output, err = cli.runCommands(configNtp)

   if output != 0:
      print("Trying to run commands [en, configure, ntp server {} prefer iburst exit".format(ntpServer))
      print("Output: ",str(output))
      print("Error: ",str(err))
      print("Could not restart NTP server. Aborting")
      raise Exception("Could not restart NTP server, output:{}. Aborting".format(str(err)))

   # polls and monitors ntpstat command for
   # synchronization status with intervals
   monitorNtpSync()


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


###################  MAIN SCRIPT  ###################

##########  IMPORT HANDLING  ##########
# Some or more of these imports could fail when running this script
# in python2 environment starting EOS 4.30.1 If that is the case, we try to run the
# script with python3. In case we cannot recover, the script will require "eosUrl" to
# perform an upgrade before it can proceed.
try:
   from SysdbHelperUtils import SysdbPathHelper
   import requests
   import Cell
except ImportError as e:
   if sys.version_info < (3,) and os.path.exists( '/usr/bin/python3' ):
      os.execl( '/usr/bin/python3', 'python3', os.path.abspath(__file__ ) )
   else:
      tryImageUpgrade( e )

if sys.version_info >= (3,):
   from urllib.parse import urlparse
else:
   from urlparse import urlparse

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
         response = subprocess.check_output( cmd, shell=True, stderr=subprocess.STDOUT,
         universal_newlines=True )
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
      proc = None
      def handleSigterm( sig, frame ):
         if proc is not None:
            proc.terminate()
         sys.exit( 127 + signal.SIGTERM )
      # The bootstrap script and challenge script anyway contain the required shebang for a
      # particualar EOS version, hence instead of re-evaluating here, we can easily just execute
      # it from that shebang itself.
      cmd = [ "chmod +x " + BOOT_SCRIPT_PATH ]
      try:
         subprocess.check_output( cmd, shell=True, stderr=subprocess.STDOUT )
      except subprocess.CalledProcessError as e:
         print( e.output )
         raise e
      print( "step 3.2.1 done, setup execution permissions for bootstrap script" )
      cmd = BOOT_SCRIPT_PATH
      os.environ['CVPROXY'] = cvproxy
      try:
         signal.signal( signal.SIGTERM, handleSigterm )
         proc = subprocess.Popen( [ cmd ], shell=True, stderr=subprocess.STDOUT,
                                  env=os.environ )
         proc.communicate()
         if proc.returncode:
            print( "Bootstrap script failed with return code {}".format(proc.returncode))
            sys.exit( proc.returncode )
      except subprocess.CalledProcessError as e:
         print( e.output )
         raise e
      print( "step 3.2.2 done, executing the fetched bootstrap script" )

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
      # urlparse in py3 parses correctly only if the url is properly introduced by //
      if not ( addr.startswith( '//' ) or addr.startswith( 'http://' ) or
               addr.startswith( 'https://' ) ):
         addr = '//' + addr
      addr = addr.replace( "apiserver", "www" )
      addrURL = urlparse( addr )
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
      self.enrollAddr = self.bootstrapURL.netloc

   def getBootstrapURL( self, addr ):
      # urlparse in py3 parses correctly only if the url is properly introduced by //
      if not ( addr.startswith( '//' ) or addr.startswith( 'http://' ) or
               addr.startswith( 'https://' ) ):
         addr = '//' + addr
      addrURL = urlparse( addr )
      if addrURL.netloc == "":
         addrURL = addrURL._replace( path="", netloc=addrURL.path )
      if addrURL.path == "":
         addrURL = addrURL._replace( path="/ztp/bootstrap" )
      if addrURL.scheme == "":
         addrURL = addrURL._replace( scheme="http" )
      return addrURL


if __name__ == "__main__":
   if cvAddr == "":
      sys.exit( "error: address to CVP missing" )
   if enrollmentToken == "":
      sys.exit( "error: enrollment token missing" )

   # restart ntp process in case a ntpServer value is passed.
   if ntpServer != "":
      configureAndRestartNTP(ntpServer)
   # check whether it is cloud or on prem
   if cvAddr.find( "arista.io" ) != -1 :
      bm = CloudBootstrapManager()
   else:
      bm = OnPremBootstrapManager()

   # run the script
   bm.run()
