"""Unattended Windows installs: autounattend.xml, the way cloud-init is.

A Linux guest gets a user, a password and its hostname from a NoCloud seed.
Windows has the same idea and none of the convenience: Setup reads
autounattend.xml from the root of any attached volume, and the file is long,
order-sensitive, and unforgiving about namespaces.

The part everyone loses a day to is the storage driver. Windows Setup has no
virtio-blk or virtio-scsi driver, so on a virtio disk it gets as far as
"Where do you want to install Windows?" with an empty list. The answer file
below points Setup at the driver on the virtio-win disc before it looks, so
the disk is simply there.

Nothing here talks to libvirt: it builds a string, and create.py turns it
into an ISO the same way it does a cloud-init seed.
"""

from __future__ import annotations

from dataclasses import dataclass

from .xmlesc import x

# Where the virtio-win disc keeps each driver, by Windows version. Setup is
# given the folder, not the .inf, and takes every driver it finds there.
VIRTIO_PATHS = {
    "w11": ("vioscsi/w11/amd64", "viostor/w11/amd64", "NetKVM/w11/amd64"),
    "w10": ("vioscsi/w10/amd64", "viostor/w10/amd64", "NetKVM/w10/amd64"),
    "2k22": ("vioscsi/2k22/amd64", "viostor/2k22/amd64", "NetKVM/2k22/amd64"),
    "2k19": ("vioscsi/2k19/amd64", "viostor/2k19/amd64", "NetKVM/2k19/amd64"),
}

# What Setup calls each edition in its own image list.
EDITIONS = (
    "Windows 11 Pro",
    "Windows 11 Home",
    "Windows 11 Enterprise",
    "Windows 10 Pro",
    "Windows 10 Home",
)

@dataclass(frozen=True)
class Unattend:
    """What the answer file needs to know. The Windows side of CloudInit."""

    user: str
    password: str = ""
    hostname: str = ""
    edition: str = "Windows 11 Pro"
    locale: str = "en-GB"
    timezone: str = "UTC"
    windows_version: str = "w11"  # which virtio driver folder to use
    autologon: bool = True
    skip_oobe: bool = True

def driver_paths(windows_version: str) -> tuple[str, ...]:
    return VIRTIO_PATHS.get(windows_version, VIRTIO_PATHS["w11"])

# Which letter WinPE gives the driver disc depends on how many volumes are
# attached and in what order, and this install has three. Setup ignores a
# driver path that is not there, so every plausible letter is listed and the
# one that exists wins - which beats guessing right most of the time.
CANDIDATE_DRIVES = ("D:", "E:", "F:", "G:")


def build_autounattend(spec: Unattend, drives=CANDIDATE_DRIVES) -> str:
    """The answer file, as a string."""
    if not spec.user.strip():
        raise ValueError("An unattended install needs a user to create")

    entries = [
        f"{drive}\\{path.replace('/', chr(92))}"
        for drive in drives
        for path in driver_paths(spec.windows_version)
    ]
    drivers = "".join(
        f"""
          <PathAndCredentials wcm:action="add" wcm:keyValue="{i + 1}">
            <Path>{x(entry)}</Path>
          </PathAndCredentials>"""
        for i, entry in enumerate(entries)
    )
    # An empty password is legal and means "no password", but the element
    # has to be absent rather than empty or Setup rejects the file.
    password = (
        f"""
            <Password>
              <Value>{x(spec.password)}</Value>
              <PlainText>true</PlainText>
            </Password>"""
        if spec.password else ""
    )
    autologon = ""
    if spec.autologon and spec.password:
        autologon = f"""
        <AutoLogon>
          <Enabled>true</Enabled>
          <Username>{x(spec.user)}</Username>
          <LogonCount>1</LogonCount>
          <Password>
            <Value>{x(spec.password)}</Value>
            <PlainText>true</PlainText>
          </Password>
        </AutoLogon>"""
    oobe = ""
    if spec.skip_oobe:
        # Windows 11 refuses to finish setup without a network and a
        # Microsoft account unless it is told not to ask.
        oobe = """
        <OOBE>
          <HideEULAPage>true</HideEULAPage>
          <HideOEMRegistrationScreen>true</HideOEMRegistrationScreen>
          <HideOnlineAccountScreens>true</HideOnlineAccountScreens>
          <HideWirelessSetupInOOBE>true</HideWirelessSetupInOOBE>
          <ProtectYourPC>3</ProtectYourPC>
        </OOBE>
        <RunSynchronous>
          <RunSynchronousCommand wcm:action="add">
            <Order>1</Order>
            <Path>reg add HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\OOBE /v BypassNRO /t REG_DWORD /d 1 /f</Path>
          </RunSynchronousCommand>
        </RunSynchronous>"""

    hostname = spec.hostname.strip() or spec.user.strip()
    return f"""<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend"
          xmlns:wcm="http://schemas.microsoft.com/WMIConfig/2002/State">
  <settings pass="windowsPE">
    <component name="Microsoft-Windows-PnpCustomizationsWinPE"
               processorArchitecture="amd64"
               publicKeyToken="31bf3856ad364e35" language="neutral"
               versionScope="nonSxS">
      <DriverPaths>{drivers}
      </DriverPaths>
    </component>
    <component name="Microsoft-Windows-International-Core-WinPE"
               processorArchitecture="amd64"
               publicKeyToken="31bf3856ad364e35" language="neutral"
               versionScope="nonSxS">
      <SetupUILanguage>
        <UILanguage>{x(spec.locale)}</UILanguage>
      </SetupUILanguage>
      <InputLocale>{x(spec.locale)}</InputLocale>
      <SystemLocale>{x(spec.locale)}</SystemLocale>
      <UILanguage>{x(spec.locale)}</UILanguage>
      <UserLocale>{x(spec.locale)}</UserLocale>
    </component>
    <component name="Microsoft-Windows-Setup" processorArchitecture="amd64"
               publicKeyToken="31bf3856ad364e35" language="neutral"
               versionScope="nonSxS">
      <DiskConfiguration>
        <WillShowUI>OnError</WillShowUI>
        <Disk wcm:action="add">
          <DiskID>0</DiskID>
          <WillWipeDisk>true</WillWipeDisk>
          <CreatePartitions>
            <CreatePartition wcm:action="add">
              <Order>1</Order><Type>EFI</Type><Size>260</Size>
            </CreatePartition>
            <CreatePartition wcm:action="add">
              <Order>2</Order><Type>MSR</Type><Size>16</Size>
            </CreatePartition>
            <CreatePartition wcm:action="add">
              <Order>3</Order><Type>Primary</Type><Extend>true</Extend>
            </CreatePartition>
          </CreatePartitions>
          <ModifyPartitions>
            <ModifyPartition wcm:action="add">
              <Order>1</Order><PartitionID>1</PartitionID>
              <Format>FAT32</Format><Label>System</Label>
            </ModifyPartition>
            <ModifyPartition wcm:action="add">
              <Order>2</Order><PartitionID>2</PartitionID>
            </ModifyPartition>
            <ModifyPartition wcm:action="add">
              <Order>3</Order><PartitionID>3</PartitionID>
              <Format>NTFS</Format><Label>Windows</Label><Letter>C</Letter>
            </ModifyPartition>
          </ModifyPartitions>
        </Disk>
      </DiskConfiguration>
      <ImageInstall>
        <OSImage>
          <InstallFrom>
            <MetaData wcm:action="add">
              <Key>/IMAGE/NAME</Key>
              <Value>{x(spec.edition)}</Value>
            </MetaData>
          </InstallFrom>
          <InstallTo><DiskID>0</DiskID><PartitionID>3</PartitionID></InstallTo>
        </OSImage>
      </ImageInstall>
      <UserData>
        <AcceptEula>true</AcceptEula>
      </UserData>
    </component>
  </settings>
  <settings pass="specialize">
    <component name="Microsoft-Windows-Shell-Setup"
               processorArchitecture="amd64"
               publicKeyToken="31bf3856ad364e35" language="neutral"
               versionScope="nonSxS">
      <ComputerName>{x(hostname[:15])}</ComputerName>
      <TimeZone>{x(spec.timezone)}</TimeZone>
    </component>
  </settings>
  <settings pass="oobeSystem">
    <component name="Microsoft-Windows-Shell-Setup"
               processorArchitecture="amd64"
               publicKeyToken="31bf3856ad364e35" language="neutral"
               versionScope="nonSxS">
      <UserAccounts>
        <LocalAccounts>
          <LocalAccount wcm:action="add">
            <Name>{x(spec.user)}</Name>
            <DisplayName>{x(spec.user)}</DisplayName>
            <Group>Administrators</Group>{password}
          </LocalAccount>
        </LocalAccounts>
      </UserAccounts>{autologon}{oobe}
    </component>
  </settings>
</unattend>
"""
