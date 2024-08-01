# CloudVision ZTPaaS Utils

![Bootstrap Script Linting Check Badge][BOOTSTRAP_LINTING_CHECK]
![Python Tests Badge][PYTHON_TESTS]

## Introduction

Arista’s Zero Touch Provisioning is used to configure a switch without user intervention. Built to leverage Arista’s Extensible Operating System (EOS), ZTP as-a-Service provides a flexible solution to onboard EOS devices into CloudVision as-a-Service.

CloudVision ZTPaaS Utils hosts different tools and scripts to support the Zero Touch Provisioning on CVaaS.

## Bootstrap Script with a Token

Bootstrap script with a token provides an alternative way of ZTP enrolling an Arista device against CVaaS. The cluster URL and the organisation wide enrollment token can be supplied by the script as opposed to the two being supplied using a USB drive. A DHCP server co-located with the Arista device can be configured to serve this bootstrap script using the bootfile-name option. This bootstrap script, then, takes over and perform all the steps necessary to initate ZTP against the correct CVaaS cluster and tenant.

- Log in to the CVaaS cluster and generate a token using the "Generate" option under "Devices/Onboard Devices" menu

- Download the custom bootstrap script and modify the "USER INPUT" section to specify the cluster URL and the enrollment token:

        ########### USER INPUT ############
        cvAddr = "www.cv-mycluster.arista.io"
        enrollmentToken = "eyJhbGciOiJSUzI1Nixxx..."

- Host the script on a server locally, and modify the DHCP server to point to this script via option-67/bootfile-name option

- Boot up the EOS device into ZTP mode. It should download the script and enroll with the desired CVaaS cluster against the correct tenant.

> Please note that the correct regional URL where the CVaaS tenant is deployed must be used for EOS versions older than 4.30. The following are the
cluster URLs used in production:

| Region | URL |
|--------|-----|
| United States 1a | `www.arista.io` |
| United States 1b | `www.cv-prod-us-central1-b.arista.io`|
| United States 1c | `www.cv-prod-us-central1-c.arista.io`|
| Canada | `www.cv-prod-na-northeast1-b.arista.io` |
| Europe West 2| `www.cv-prod-euwest-2.arista.io` |
| Japan| `www.cv-prod-apnortheast-1.arista.io` |
| Australia | `www.cv-prod-ausoutheast-1.arista.io` |
| United Kingdon | `www.cv-prod-uk-1.arista.io` |

!!! Warning

    URLs without `www` are not supported.

## Troubleshooting tips

### ZTP-4-EXEC_SCRIPT_SIGNALED: Config script exited with an uncaught signal. Signal code: 1

This usually indicates a problem executing the config script. In most cases this happens when the script is edited on a Microsoft Windows machine due
to which each line is ending in `Windows(CR LF)` instead of `Unix(LF)`. There are multiple ways to replace `CR LF` with `LF`, one way is to use Notepad++,
click on Edit - EOL Conversion and select `Unix(LF)` and save the file. This is also described in [A Practical Guide to Zero Touch Provisioning (ZTP) in CloudVision as a Service (CVaaS)](https://arista.my.site.com/AristaCommunity/s/article/A-Practical-Guide-to-Zero-Touch-Provisioning-ZTP-in-Cloud-Vision-as-a-Service-CVaaS) Community central article.

[BOOTSTRAP_LINTING_CHECK]: https://github.com/aristanetworks/cloudvision-ztpaas-utils/actions/workflows/bootstrap-linting.yaml/badge.svg
[PYTHON_TESTS]: https://github.com/aristanetworks/cloudvision-ztpaas-utils/actions/workflows/python-tests.yaml/badge.svg
