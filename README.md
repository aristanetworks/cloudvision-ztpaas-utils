# CloudVision ZTPaaS Utils



## Introduction

Arista’s Zero Touch Provisioning is used to configure a switch without user intervention. Built to fully leverage the power of Arista’s Extensible Operating System (EOS), ZTP as-a-Service provides a flexible solution to onboard EOS devices into CloudVision as-a-Service

CloudVision ZTPaaS Utils hosts different tools and scripts to support the ZTPaaS usecase on CloudVision as-a-service.



## Custom Bootstrap Script

ZTP as-a-Service can be enabled via a custom bootstrap script and using a DHCP server option to point to that bootstrap script. This method obviates the need for supplying enrollment token using USB.



### Using Custom Bootstrap Script

- Log in to the Clodvision as-a-service cluster and generate a token using the "Generate" option under "Devices/Onboard Devices" menu

- Download the custom bootstrap script and modify the "USER INPUT" section to specify the cluster url and the enrollment token:

        ########### USER INPUT ############
        cvAddr = "www.cv-mycluster.arista.io"
        enrollmentToken = "eyJhbGciOiJSUzI1Nixxx..."

- Host the script on a server locally, and modify the DHCP server to point to this script via option-67/bootfile-name option

- Boot up the EOS device into ZTP mode. It should download the script and enroll with the desired CVaaS cluster against the correct tenant.
