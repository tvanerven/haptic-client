.PHONY: all update install_dev_tools install_skinetic_sdk

# Default target
all: update install_dev_tools install_skinetic_sdk

# Update the package lists and upgrade existing packages
update:
	sudo dnf update && sudo dnf upgrade -y

# Install development tools
install_dev_tools:
	sudo dnf install -y wget vim unzip

# Download and install Skinetic SDK
install_skinetic_sdk:
	wget https://storage.googleapis.com/skinetic-sdk/1.6.2/SkineticSDK_1.6.2_linux_x64.zip
	unzip SkineticSDK_1.6.2_linux_x64.zip
	sudo rm SkineticSDK_1.6.2_linux_x64.zip

	sudo cp ./SkineticSDK/70-skinetic-hid.rules /etc/udev/rules.d/
	sudo service systemd-udevd restart
