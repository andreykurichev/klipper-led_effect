## Installation

### Automatic installation

The module can be installed into a existing Klipper installation with an install script. 

    cd ~
    git clone https://github.com/andreykurichev/klipper-led_effect.git
    cd klipper-led_effect
    ./install-led_effect.sh

Add the updater section to moonraker.conf and restart moonraker to receive 
updates:

    [update_manager led_effect]
    type: git_repo
    path: ~/klipper-led_effect
    origin: https://github.com/andreykurichev/klipper-led_effect.git
    is_system_service: False

## Uninstall

Remove all led_effect definitions in your Klipper configuration and the updater
section in the Moonraker configuration. Then run the script to remove the link:

    cd ~
    cd klipper-led_effect
    ./install-led_effect.sh -u
