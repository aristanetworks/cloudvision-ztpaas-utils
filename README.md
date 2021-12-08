# CloudVision ZTPaaS Utils



## Introduction

Arista’s Zero Touch Provisioning is used to configure a switch without user intervention. Built to leverage Arista’s Extensible Operating System (EOS), ZTP as-a-Service provides a flexible solution to onboard EOS devices into CloudVision as-a-Service.

CloudVision ZTPaaS Utils hosts different tools and scripts to support the Zero Touch Provisioning on CVaaS.



## Custom Bootstrap Script

The custom bootstrap script method can be used to ZTP enroll an Arista device in CVaaS. This method obviates the need for supplying an enrollment token, or the cluster URL in a USB drive. A locally hosted DHCP server can be configured to point the Arista device to this custom bootstrap script using the bootfile-name option.

### Using the custom bootstrap script

- Log in to the CVaaS cluster and generate a token using the "Generate" option under "Devices/Onboard Devices" menu

- Download the custom bootstrap script and modify the "USER INPUT" section to specify the cluster URL and the enrollment token:

        ########### USER INPUT ############
        cvAddr = "www.cv-mycluster.arista.io"
        enrollmentToken = "eyJhbGciOiJSUzI1Nixxx..."

- Host the script on a server locally, and modify the DHCP server to point to this script via option-67/bootfile-name option

- Boot up the EOS device into ZTP mode. It should download the script and enroll with the desired CVaaS cluster against the correct tenant.
