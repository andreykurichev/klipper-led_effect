## Installation

### Automatic installation

The module can be installed into a existing Klipper installation with an install script. 

    cd ~
    git clone https://github.com/andreykurichev/klipper-led_effect.git
    cd klipper-led_effect
    ./install-led_effect.sh

If your directory structure differs from the usual setup you can configure the
installation script with parameters:

    ./install-led_effect.sh [-k <klipper path>] [-s <klipper service name>] [-c <configuration path>]

### Manual installation
Clone the repository:

    cd ~
    git clone https://github.com/julianschill/klipper-led_effect.git

Stop Klipper:

    systemctl stop klipper

Link the file in the Klipper directory (adjust the paths as needed):

    ln -s klipper-led_effect/led_effect.py ~/klipper/klippy/extras/led_effect.py

Start Klipper:

    systemctl start klipper

Add the updater section to moonraker.conf and restart moonraker to receive 
updates:

    [update_manager led_effect]
    type: git_repo
    path: ~/klipper-led_effect
    origin: https://github.com/julianschill/klipper-led_effect.git
    is_system_service: False

## Uninstall

Remove all led_effect definitions in your Klipper configuration and the updater
section in the Moonraker configuration. Then run the script to remove the link:

    cd ~
    cd klipper-led_effect
    ./install-led_effect.sh -u

If your directory structure differs from the usual setup you can configure the
installation script with parameters:

    ./install-led_effect.sh -u [-k <klipper path>] [-s <klipper service name>] [-c <configuration path>]

If that fails, you can delete the link in Klipper manually:

    rm ~/klipper/klippy/extras/led_effect.py

Delete the repository (optional):

    cd ~
    rm -rf klipper-led_effect

## Configuration


