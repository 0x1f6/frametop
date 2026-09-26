#!/bin/bash
# Installed to /etc/steamframe/bt-fixups.sh by setup/bluetooth/install.sh.
# Runs as root from steamframe-bt-fixups.service after every bluetoothd start,
# and by hand after pairing a new LE device: sudo /etc/steamframe/bt-fixups.sh
#
# Works around two things that stop Bluetooth LE mice like the Swiftpoint Z3
# from reconnecting:
#  1. BlueZ 5.79 never sets the ADDRESS_RESOLUTION device flag, so kernel 6.18
#     never programs a bonded device's IRK into the controller's resolving list,
#     and devices that advertise with private addresses never reconnect.
#     Fixed upstream in BlueZ f1fb4f95f4 ("core: Fix not resolving addresses").
#     Workaround: set flags 0x6 on every bonded LE device that has an IRK.
#  2. The Z3 doesn't accept the host's IRK when pairing, so it only knows the
#     Frame's public address. Controller privacy has to be off.
set -u

# btmgmt quits before the reply unless stdin stays open for a moment.
mgmt() { sleep 2 | btmgmt --index 0 "$@" 2>&1; }

# Wait for the controller to come up and be powered on (can take a while after boot).
for _ in $(seq 60); do
  mgmt info | grep -q "current settings:.* powered" && break
  sleep 1
done

if mgmt info | grep -q "current settings:.* privacy"; then
  mgmt power off >/dev/null
  mgmt privacy off >/dev/null
  mgmt power on >/dev/null
  echo "privacy turned off"
fi

ctrl=$(mgmt info | awk '/^\s*addr /{print $2; exit}')
for info in /var/lib/bluetooth/"$ctrl"/*/info; do
  [ -f "$info" ] || continue
  grep -q '^\[IdentityResolvingKey\]' "$info" || continue
  addr=$(basename "$(dirname "$info")")
  case $(sed -n 's/^AddressType=//p' "$info") in
    public) type=1 ;;
    *) type=2 ;;
  esac
  echo "$(mgmt set-flags -f 0x6 -t "$type" "$addr" | tail -n 1)"
done
exit 0
