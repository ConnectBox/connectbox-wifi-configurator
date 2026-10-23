# -*- coding: utf-8 -*-
"""Console script for wifi_configurator."""
import functools
import os
import random
import subprocess
import sys
import json
import click
import configobj
import jinja2
import pyric
import pyric.pyw as pyw
import logging
import time



SYSLOG_TAG = "wifi-configurator"
DEFAULT_SSID = " - Free Media"
DEFAULT_CHANNEL = "7"
INTERFACE = 'wlan0'




@functools.lru_cache()
def hostapd_conf_as_config(filename):
    """Load and cache a hostapd.conf file as a ConfigObj dict.

    Uses lru_cache so that multiple get_current_*() calls and the main()
    function all share the same parsed object without re-reading the file.

    Accepts '-' (stdin) as a special value so the tool can be driven from
    a pipe during testing.  Falls back to an empty ConfigObj if the file
    does not exist, allowing individual get_current_*() calls to return
    their built-in defaults.

    Parameters
    ----------
    filename : str or file
        Path to hostapd.conf, or sys.stdin if '-' was passed on the CLI.

    Returns
    -------
    configobj.ConfigObj
        Parsed configuration, or an empty ConfigObj on missing file.
    """
    if filename == sys.stdin or os.path.exists(filename):
        return configobj.ConfigObj(filename)
    click.echo("Warning: unable to load specific config file: %s" % (filename,))
    return configobj.ConfigObj()


# The following get_current_... methods are crafted to take steps to recover
#  from a busted config file by defaulting to values that will allow hostapd
#  to start

def get_current_ssid(config):
  """Return the SSID to broadcast, derived from the brand name in brand.j2.

  Reads the 'Brand' key from /usr/local/connectbox/brand.j2 (JSON) and
  appends DEFAULT_SSID (' - Free Media') to form the full SSID string.
  Falls back to 'ConnectBox - Free Media' if the file is missing, empty,
  or contains invalid JSON — so hostapd can always start even on a partially
  provisioned device.

  Parameters
  ----------
  config : configobj.ConfigObj
      Parsed hostapd.conf (not currently used; kept for API symmetry with
      other get_current_* functions).

  Returns
  -------
  str
      Full SSID string, e.g. 'MyOrg - Free Media'.
  """
# Using a dictionary and json to store Branding stuff
# Set a fallback name in case the file is 'busted'
  brand_name = "ConnectBox"

# Read the dictionary
  try:
    with open('/usr/local/connectbox/brand.j2') as f:
      try:
          data = f.read()
          f.close()
          js = json.loads(data)
          brand_name = js["Brand"]
      except (FileNotFoundError, json.JSONDecoderError, KeyError) as e:
          Logging.warning(f"Config busted or missing: {e}, Using fallback  ")
    return (brand_name + DEFAULT_SSID)
  except (FileNotFoundError) as e:
    Logging.warning(f"Config file missing: {e}, using fallback ")
    return ("ConnectBox" + DEFAULT_SSID)


def get_current_interface(config):
    """Return the WiFi interface name to use for the access point.

    Reads /usr/local/connectbox/wificonf.txt which is written by the
    neo_batterylevelshutdown service after it detects and assigns WiFi
    interfaces.  The file format is line-delimited with the AP interface
    on line 0 and the client interface on line 1, each in 'IF=wlanX' format.

    Falls back to the module-level INTERFACE constant ('wlan0') if the file
    cannot be read, so the AP can start even without HAT service running.

    Parameters
    ----------
    config : configobj.ConfigObj
        Parsed hostapd.conf (not currently used).

    Returns
    -------
    str
        Interface name, e.g. 'wlan0' or 'wlan1'.
    """
# Since we now have in the neo_battery_shutdown a wifi channel identifier
# we want to use the listed interface for our configuration
    try:
        with open('/usr/local/connectbox/wificonf.txt') as f:
            data = f.read()
            f.close()
            a = data.split("\n")                            #Split the file into lines.  A[0] = AP A[1]=Client A[2]=######END####
            click.echo("access point line is: %s" % (a[0],))
            click.echo("client line is: %s" % (a[1],))
            a = a[0].split("IF=")                               #Access Point is first line with AccessPointIF=wlanX
            click.echo("AP is: %s" % (a[1],))

            return(a[1])
    except:
        click.echo("Failure!! could not read the wificonf.txt file")
        return(INTERFACE)

def get_current_channel(config):
    """Return the current hostapd channel, defaulting to DEFAULT_CHANNEL ('7').

    Parameters
    ----------
    config : configobj.ConfigObj
        Parsed hostapd.conf.

    Returns
    -------
    int
        Channel number.
    """
    return int(config.get("channel", DEFAULT_CHANNEL))


def get_current_ht_capab(config):
    """Return the current HT capabilities string from hostapd.conf.

    Returns an empty string if not set, which is safe — hostapd will start
    with basic HT20 support when ht_capab is absent.

    Parameters
    ----------
    config : configobj.ConfigObj

    Returns
    -------
    str
        hostapd ht_capab value, e.g. '[HT20][SHORT-GI-20]'.
    """
    return config.get("ht_capab", "")


def get_current_country_code(config):
    """Return the country_code currently set in hostapd.conf.

    Returns an empty string if not set.  An empty country code is valid for
    hostapd but suppresses regulatory-domain enforcement.

    Parameters
    ----------
    config : configobj.ConfigObj

    Returns
    -------
    str
        Two-letter ISO country code or ''.
    """
    return config.get("country_code", "")

def get_current_ac_mode(config):
    """Return the ieee80211ac flag from hostapd.conf (0 = disabled, 1 = enabled).

    Parameters
    ----------
    config : configobj.ConfigObj

    Returns
    -------
    str
        '0' or '1'.
    """
    return config.get("ieee80211ac", "0")


def get_current_wpa_passphrase(config):
    """Return the WPA passphrase from hostapd.conf, or '' if none is set.

    An empty string signals that the AP should run open (no password), which
    is the ConnectBox default.  None is never returned so callers can do
    simple truthiness checks without handling None separately.

    Parameters
    ----------
    config : configobj.ConfigObj

    Returns
    -------
    str
        WPA passphrase (8-63 chars) or '' for an open AP.
    """
    # If it's not set, then pass back an empty string and we can follow
    #  the logic as if we're turning off password protection
    return config.get("wpa_passphrase", "")


def cb_handle_wpa_passphrase(ctx, _, value):
    """Click callback that validates and normalises the --wpa-passphrase option.

    Three distinct cases must be handled:
    - None (option not given): re-use the passphrase already in hostapd.conf.
    - '' (empty string): disable WPA and make the AP open — valid and intentional.
    - Non-empty string: validate WPA length (8-63 chars) and return as-is.

    A simple equality check cannot distinguish None from '' here, which is why
    this explicit three-way check is needed rather than a default= on the option.

    Parameters
    ----------
    ctx : click.Context
    _ : click.Option (unused)
    value : str or None
        Raw CLI value for --wpa-passphrase.

    Returns
    -------
    str
        Validated passphrase, '' for open AP, or re-used existing value.

    Raises
    ------
    click.BadParameter
        If value is a non-empty string shorter than 8 or longer than 63 chars.
    """
    # IF the passphrase is None, re-use whatever is already in config
    # We can't do a basic equality check here, because an empty string is
    #  used to disable password protection, and that's different to not
    #  specifying a wpa_passphrase (which is None when it's unset)
    if value is None:
        config = hostapd_conf_as_config(ctx.params["filename"])
        return get_current_wpa_passphrase(config)

    # An empty passphrase disables password auth so it's valid
    if value == "":
        return value

    # Otherwise, valid WPA passphrases are between 8 and 63 chars inclusive
    if 8 <= len(value) <= 63:
        return value

    raise click.BadParameter('Passphrase must be 8-63 characters long or empty')


def cb_handle_filename(_, _2, value):
    """Click callback that translates the '-' filename shorthand to sys.stdin.

    Allows the tool to be used in a pipe: `cat hostapd.conf | wifi-configurator -f -`
    The resulting sys.stdin value is detected by hostapd_conf_as_config() and
    cb_handle_output() to handle stdin/stdout pairing correctly.

    Parameters
    ----------
    _ : click.Context (unused)
    _2 : click.Option (unused)
    value : str
        Raw CLI value for -f/--filename.

    Returns
    -------
    str or file
        sys.stdin if value is '-', otherwise the raw path string.
    """
    if value == "-":
        return sys.stdin

    return value


def cb_handle_output(ctx, _, value):
    """Click callback that resolves the -o/--output destination.

    Default behaviour (no -o given) is to write back to the same file that was
    read, so a plain invocation updates hostapd.conf in place.  When the input
    came from stdin, the output defaults to stdout so the tool can participate
    in a pipeline.

    Parameters
    ----------
    ctx : click.Context
        Used to retrieve the already-resolved 'filename' parameter.
    _ : click.Option (unused)
    value : str or None
        Raw CLI value for -o/--output.

    Returns
    -------
    str or file
        Resolved output destination: a file path, sys.stdout, or the input filename.
    """
    if not value:
        if ctx.params["filename"] == sys.stdin:
            return sys.stdout

        return ctx.params["filename"]

    # If we read the config on stdin, we need a diff place to write the output
    if value == "-":
        return sys.stdout

    return value


@click.command()
# This must be an eager option because other options reference it in their
#  callbacks
@click.option('-f', '--filename',
              callback=cb_handle_filename,
              default="/etc/hostapd/hostapd.conf",
              is_eager=True,
              help="Input file to be used for values not being updated. "
                   "Defaults to /etc/hostapd/hostapd.conf")
@click.option('-i', '--interface',
              default=INTERFACE,
              help="Wifi interface name. Defaults to wlan0")
# Add length check (32 octets)
@click.option('-s', '--ssid',
              help="Set a new ssid for the access point")
# Add validation (possibly even taking region into account)
@click.option('-c', '--channel',
              type=int,
              help="Set a new channel for the access point")
@click.option('-o', '--output',
              callback=cb_handle_output,
              help="Destination for updated configuration file. "
                   "Defaults to filename. - writes to stdout")
@click.option('-p', '--wpa-passphrase',
              callback=cb_handle_wpa_passphrase,
              help="Access point passphrase (8-63 characters long). "
                   "Empty passphrase makes the access point open")
@click.option('--set-country-code',
              is_flag=True,
              help="Do a best-effort attempt to infer the local country "
                   "code by sniffing for other APs")
@click.option('--sync/--no-sync',
              default=True,
              help="Performs a filesystem sync after writing changes")

# pylint: disable=too-many-arguments,too-many-locals
def main(filename, interface, ssid, channel, output, wpa_passphrase, sync,
         set_country_code):
    """Generate a hostapd.conf for the ConnectBox WiFi access point.

    This is the primary entry point for the wifi-configurator CLI.  It reads
    the existing hostapd.conf, fills in any unspecified values from the running
    config or auto-detection, renders a new hostapd.conf via Jinja2, and
    restarts the relevant services so changes take effect immediately.

    Processing sequence:
    1. Load existing hostapd.conf to provide defaults for any options not
       specified on the command line.
    2. Identify the WiFi adapter via adapters.factory() so the correct
       ht_capab/vht_capab flags are written into the template.
    3. If --set-country-code or no channel given, run 'iw dev scan' to detect
       the regulatory domain and find an uncontested channel.
    4. Validate the channel against the legal channel list for the country.
    5. Render hostapd.conf and wpa_supplicant.conf from Jinja2 templates.
    6. Optionally sync the filesystem to prevent corruption on power loss.
    7. Restart hostapd, wpa_supplicant, and dnsmasq so changes are live.

    Parameters are supplied by Click from the decorated options above.
    """
    sys.path.append('/usr/local/connectbox/wifi_configurator_venv/lib/python3.9/site-packages/wifi_configurator')
    import adapters
    import scan

    config = hostapd_conf_as_config(filename)
    # Could use a callback and access filename parameter in the decorator,
    #  (filename possibly needs is_eager) or by subclassing click.Option
    if not ssid:
        ssid = get_current_ssid(config)

    interface = get_current_interface(config)

    wifi_adapter = adapters.factory(interface)
    logging.info("ssid is"+str(ssid)+"interface is:"+str(interface)+"wifi adapter: "+str(wifi_adapter))
    # We deliberately only instantiate pyw.getcard for as small a set of
    #  parameters as possible because pyw gets sad if operations are
    #  attempted on a device that does not support nl80211 and we want to
    #  be to do as many things as possible in simulations
    scan_output = ""
    if set_country_code or not channel:
        try:
            if pyw.iswireless(interface):
                wlan_if = pyw.getcard(interface)
                scan_output = scan.get_scan_output(wlan_if)
            else:
                click.echo("Interface %s is not a wifi interface. Won't be "
                           "able to infer country code or do automatic "
                           "channel selection" % (interface,))
        except pyric.error:
            click.echo("Unable to query interface %s with pyw. Won't be "
                       "able to infer country code or do automatic "
                       "channel selection" % (interface,))

    # Retrieve the previous cc now, given we have so many fallback cases
    country_code = get_current_country_code(config)
    if set_country_code:
        scanned_cc = scan.detect_regdomain(scan_output)
        # Only use the scanned cc if it's non-empty
        if scanned_cc[0]:
            country_code = scanned_cc
        else:
            click.echo("Could not do wifi scan. Using previous country code")
        click.echo("Country code is: %s" % (country_code,))
    valid_channels_for_cc = scan.channels_for_country(country_code)
    if not channel:
        # Choose an uncontested channel, or a random one if there aren't any
        # MEH that we respecify wlan_if when we've used it to scan just a bit
        #  earlier
        channel = scan.get_available_uncontested_channel(
            valid_channels_for_cc, scan_output
        )
        if (not channel and len(valid_channels_for_cc) > 0):
            channel = random.choice(valid_channels_for_cc)
            click.echo("No uncontested channels. Choosing %s at random" %
                       (channel,))
        else:
            click.echo("Channel %s is uncontested and is now the new channel" %
                       (channel,))
           
    else:
        if channel not in valid_channels_for_cc:
            raise click.BadParameter(
                "Channel %s is not valid for country code %s. Valid choices "
                "are %s. Exiting" % (
                    channel,
                    country_code,
                    ",".join([str(i) for i in valid_channels_for_cc])))
    logging.info("AP channel is now: "+str(channel))
    res = os.system( "ifdown "+interface)
    res = os.system( "systemctl stop hostapd")
    file_loader = jinja2.PackageLoader('wifi_configurator')
    env = jinja2.Environment(
        loader=file_loader,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template('hostapd.conf.j2')
    rendered = template.stream(
        interface=interface,
        ssid=ssid,
        channel=channel,
        country_code=country_code,
        wifi_adapter=wifi_adapter,
        wpa_passphrase=wpa_passphrase,
    )
    rendered.dump(output)
    res = os.system("systemctl stop wpa_supplicant")
# get the version of python so we can use it in a directory refernce
    pipe = os.popen("ls /usr/local/connectbox/wifi_configurator_venv/lib").read()
    pipe = pipe.strip('\n')
    pipe = "cp /etc/wpa_supplicant/wpa_supplicant.conf /usr/local/connectbox/wifi_configurator_venv/lib/"+pipe+"/site-packages/wifi_configurator/templates/"
    click.echo("Final command is: %s" % (pipe,))
    res = os.system( pipe )
    if res < 0:
       click.echo("FAILED !! could not copy wpa_supplicant.conf to the template directory")
    template = env.get_template('wpa_supplicant.conf')
    rendered = template.stream(
        country=country_code)
    rendered.dump('/etc/wpa_supplicant/wpa_supplicant.conf')
    # Some filesystems don't write to disk for a while, which can lead to
    #  corruption in files. A corrupt hostapd.conf may well brick a device
    #  in the field so let's avoid that risk.
    # Related: https://github.com/ConnectBox/connectbox-pi/issues/220
    if sync and output != sys.stdout:
        subprocess.run("/bin/sync")
    res = os.system("systemctl start wpa_supplicant")
    res = os.system("systemctl start hostapd")
    res = os.system("ifup "+interface)
    res = os.system("systemctl restart hostapd")
    res = os.system("systemctl restart dnsmasq")
    time.sleep(4)
    return 0


if __name__ == "__main__":
    main()
