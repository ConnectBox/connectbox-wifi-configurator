import collections
import functools
import os
from pathlib import Path
import subprocess
import re
import time
import click
import configobj
import pyric.pyw as pyw
import pyric.utils.channels as channels
import pprint
import random


# OFDM. We don't use DSSS, so their 22MHz width doesn't matter
CHANNEL_WIDTH_MHZ_24 = 20
NO_CHANNEL = 0


class ActiveWifiInterface:
    """Context manager that ensures a WiFi interface is up for the duration of a scan.

    Scanning requires the interface to be in the 'up' state.  This manager
    records the interface's state on entry, brings it up if needed, and
    restores it to its original state on exit — so running the configurator
    on an interface that was intentionally down does not leave it up afterwards.

    Usage::

        with ActiveWifiInterface(wlan_if) as awi:
            if not awi:
                return ""   # interface could not be brought up
            # perform scan here

    The __enter__ return value is False if the interface was already up but
    pyw.isup() failed, signalling to the caller that scan results may be empty.
    """

    STATE_ACTIVE = "up"
    STATE_INACTIVE = "down"

    def __init__(self, wlan_if):
        """Record the current state of wlan_if so __exit__ can restore it.

        Parameters
        ----------
        wlan_if : pyric card object
            The wireless interface card, as returned by pyw.getcard().
        """
        self.wlan_if = wlan_if
        if pyw.isup(self.wlan_if):
            self.initial_state = self.STATE_ACTIVE
        else:
            self.initial_state = self.STATE_INACTIVE

    def __enter__(self):
        """Bring the interface up if necessary and return readiness status.

        Returns True if the interface is ready to scan (either was already up
        or was successfully brought up).  Returns False if it was already up
        but pyw.isup() returned False, which should not happen in practice.
        """
        if not pyw.isup(self.wlan_if):
            pyw.up(self.wlan_if)
            return True

        # Good to go if it's already active, but not otherwise
        return self.initial_state == self.STATE_ACTIVE

    def __exit__(self, *args):
        """Restore the interface to its pre-scan state.

        If the interface was down before the scan, bring it back down so the
        system is left in the same configuration as before the configurator ran.
        """
        # Leave things the way you found them, like your parents said
        if self.initial_state == self.STATE_INACTIVE:
            pyw.down(self.wlan_if)


def get_country_count_from_iw_output(iw_output):
    """Count how many nearby APs advertise each country code in their beacons.

    Parses 'iw dev scan' output line-by-line looking for 'Country: XX' fields.
    Each occurrence of a country code increments its counter.  Used by
    get_consensus_regdomain_from_iw_output() to pick the most common code.

    Parameters
    ----------
    iw_output : str
        Raw stdout from 'iw dev <if> scan'.

    Returns
    -------
    collections.Counter
        Mapping of country-code string to occurrence count.
    """
    c = collections.Counter()
    for line in iw_output.split("\n"):
        # e.g. '        Country: TR     Environment: Indoor/Outdoor
        line = line.strip()
        if not line.startswith("Country:"):
            continue

        fields = re.split(r'\s+', line)
        if len(fields) > 1:
            c[fields[1]] += 1

    return c


def get_consensus_regdomain_from_iw_output(iw_output):
    """Return the most commonly advertised country code seen in nearby AP beacons.

    Calls get_country_count_from_iw_output() to tally all country codes in the
    scan, then picks the plurality winner.  Returns an empty string if no APs
    were found or none advertised a country code.

    Parameters
    ----------
    iw_output : str
        Raw stdout from 'iw dev <if> scan'.

    Returns
    -------
    str
        Two-letter ISO country code (e.g. 'US', 'TR') or '' if undetermined.
    """
    cnter = get_country_count_from_iw_output(iw_output)
    most_common = cnter.most_common(1)
    if most_common:
        return most_common[0][0]

    return ""


@functools.lru_cache()
def get_scan_output(wlan_if):
    """Run 'iw dev scan' and return the raw output, retrying up to 5 times.

    Uses ActiveWifiInterface to bring the interface up if needed.  Retries
    with a 2-second sleep between attempts because the first scan after
    bringing an interface up sometimes returns empty output.

    Results are cached with lru_cache so multiple callers in the same process
    (country-code detection + channel selection) share the same scan output
    without triggering a second radio scan.

    Parameters
    ----------
    wlan_if : pyric card object
        The wireless interface card, as returned by pyw.getcard().

    Returns
    -------
    str
        Raw 'iw dev scan' output, or '' if the interface could not be brought
        up or scan produced no output after 5 retries.
    """
    with ActiveWifiInterface(wlan_if) as awi:
        if not awi:
            return ""
        a = ""
        b = 0
        # Retry loop: a freshly-up interface may return empty on the first scan.
        while a == "" and b < 5:
            iw = subprocess.run([
                "/sbin/iw",
                "dev",
                wlan_if.dev,
                "scan"
            ], stdout=subprocess.PIPE)
            a = iw.stdout.decode("utf-8")
            b += 1
            if a == "": time.sleep(2)
    return a


def get_freq_signal_tuples_from_iw_output(iw_output):
    """Parse 'iw dev scan' output into a list of (frequency_MHz, signal_dBm) tuples.

    Walks the output line by line, tracking the current BSS block.  Each time a
    new 'BSS' header is encountered the previous block's (freq, signal) pair is
    committed to the result list.  The final BSS block is committed after the
    loop.

    Parameters
    ----------
    iw_output : str
        Raw stdout from 'iw dev <if> scan'.

    Returns
    -------
    list of (int, float)
        Each tuple is (centre_frequency_in_MHz, signal_strength_in_dBm).
        Duplicate frequencies (multiple APs on the same channel) are kept so
        the caller can identify congested channels.
    """
    freq_signal_tuples = []
    freq = 0
    signal = 0.0
    for line in iw_output.split("\n"):
        line = line.strip()
        if line.startswith("BSS"):
            # New BSS block — commit the previous block's reading if we have one.
            if freq and signal:
                freq_signal_tuples.append((freq, signal))
            continue

        # freq: 2412
        if line.startswith("freq:"):
            _, freq_str = line.split(":")
            freq = int(float(freq_str))   # newer iw prints "2447.0" (Trixie, 2026-11-04)
            continue

        # signal: -48.00 dBm
        if line.startswith("signal:"):
            _, signal_str = line.split(":")
            signal = float(signal_str.strip().split(" ")[0])
            continue

    # Process the reading from the last BSS, assuming there was one
    if freq and signal:
        freq_signal_tuples.append((freq, signal))
    return freq_signal_tuples


def get_max_signal_at_each_freq(freq_signal_tuples):
    """Build a map of frequency → strongest observed signal level.

    When multiple APs occupy the same frequency, only the strongest signal is
    retained.  This gives the clearest picture of which channels are actually
    in use near the device.

    Parameters
    ----------
    freq_signal_tuples : list of (int, float)
        As returned by get_freq_signal_tuples_from_iw_output().

    Returns
    -------
    dict mapping int → float
        {frequency_MHz: max_signal_dBm}
    """
    freq_signal_map = {}
    for freq, signal in freq_signal_tuples:
        if freq in freq_signal_map:
            if signal > freq_signal_map[freq]:
                freq_signal_map[freq] = signal
        else:
            freq_signal_map[freq] = signal


def channel_overlaps_with_others(all_channel, channel_list):
    """Remove channels from all_channel that appear in channel_list and return a random survivor.

    Mutates all_channel in place, removing every channel that is also in
    channel_list (i.e. channels already in use by nearby APs).  Returns a
    random choice from the remaining uncontested channels, or NO_CHANNEL (0)
    if all channels are contested.

    Parameters
    ----------
    all_channel : list of int
        Full list of channels available under the current regulatory domain.
        Modified in place.
    channel_list : list of int
        Channels observed in use by nearby APs.

    Returns
    -------
    int
        A randomly selected uncontested channel, or NO_CHANNEL (0) if none remain.
    """
    # Remove each used channel from all_channel using index-walk so we can
    # mutate the list safely without creating a copy.
    for channel in channel_list:
        x = 0
        y = len(all_channel)
        while (x < y):
            if all_channel[x] == channel:
                all_channel.pop(x)
                y -= 1
                x = y
            else:
                x += 1

    if len(all_channel) == 0:
        return NO_CHANNEL
    else:
        return (random.choice(all_channel))


def get_available_uncontested_channel(all_available_channels, scan_output):
    """Select a channel that is not currently used by any nearby AP.

    Extracts in-use channels from the scan output, logs available vs. used
    sets for debugging, then delegates to channel_overlaps_with_others() for
    the final selection.

    Parameters
    ----------
    all_available_channels : list of int
        Channels permitted by the regulatory domain for this country code.
    scan_output : str
        Raw 'iw dev scan' output.

    Returns
    -------
    int
        An uncontested channel number, or NO_CHANNEL (0) if all are in use.
    """
    freq_signal_tuples = get_freq_signal_tuples_from_iw_output(scan_output)
    used_channels = [
        channels.ISM_24_F2C[freq] for freq, _ in freq_signal_tuples
        if freq in channels.ISM_24_F2C  # ignore non 2.4Ghz for the moment
    ]
    click.echo("Available channels are: %s" %
               (",".join([str(c) for c in all_available_channels])))
    click.echo("Used channels are: %s" %
               (",".join([str(c) for c in used_channels])))
    channel = channel_overlaps_with_others(all_available_channels, used_channels)
    click.echo("slected channel is: %s" % channel)
    return channel

def detect_regdomain(scan_output):
    """Determine the regulatory domain (country code) to use for this device.

    Checks /etc/default/crda for a REGDOMAIN override first — if an operator
    has explicitly set a country code there, it is used unconditionally so
    the device respects local law even if nearby APs advertise a different code.

    Falls back to scanning nearby AP beacons and picking the plurality country
    code via get_consensus_regdomain_from_iw_output().

    Parameters
    ----------
    scan_output : str
        Raw 'iw dev scan' output, used only when no crda override is set.

    Returns
    -------
    str
        Two-letter ISO country code (e.g. 'US') or '' if undetermined.
    """
    crda_config = Path("/etc/default/crda")
    c = configobj.ConfigObj(crda_config.as_posix())
    # Treat any REGDOMAIN setting in crda as a deliberate override
    regdomain = c.get("REGDOMAIN", "")
    if regdomain:
        return regdomain
    return get_consensus_regdomain_from_iw_output(scan_output)

def get_country_rules_block(country_code, lines):
    """Extract the lines belonging to a single country's entry in regdbdump output.

    regdbdump output groups rules by country; each country block starts with a
    'country XX:' header and ends at the next blank line.  This function
    returns just the lines for the requested country_code, including the header.

    Parameters
    ----------
    country_code : str
        Two-letter ISO country code to find (e.g. 'US').
    lines : list of str
        All lines from regdbdump stdout.

    Returns
    -------
    list of str
        Lines of the matching country block, or [] if not found.
    """
    in_country_block = False
    country_lines = []
    for line in lines:
        if in_country_block:
            if line.strip():
                country_lines.append(line)
                continue
            else:
                # We're at the end of the country block
                return country_lines
        # Match the "country XX:" header only: the code also appears inside
        #  other lines (AU in AUTO-BW, IR in NO-IR, JP in DFS-JP), which picked
        #  the wrong block for AU, NO, IR, TD, DM, DO, SE, ET and JP.
        elif not line.startswith("country %s:" % country_code):
            continue
        in_country_block = True
        country_lines.append(line)
        continue
    return country_lines


def get_frequency_blocks_from_country_block(country_code, lines):
    """Parse a country's regdb rules block into (lower_MHz, upper_MHz) frequency ranges.

    Each frequency line in regdbdump output looks like:
        (2457.000 - 2482.000 @ 20.000), (20.00), (N/A), NO-IR, AUTO-BW

    NO-OFDM ranges are excluded because we can only use OFDM channels.  When a
    NO-OFDM range overlaps the top of the previous block (e.g. Japan channel 14,
    or the 'world' 00 domain's channels 12-13), the previous block's upper
    boundary is trimmed to where the NO-OFDM range begins.

    Parameters
    ----------
    country_code : str
        Two-letter ISO country code (used only for logging).
    lines : list of str
        Country block lines as returned by get_country_rules_block(), including
        the 'country XX:' header on lines[0].

    Returns
    -------
    list of (float, float)
        Each tuple is (lower_frequency_MHz, upper_frequency_MHz) for one
        contiguous OFDM-capable band.
    """
    freqency_blocks = []
    block_lower_point = 0
    block_upper_point = 0
    # Drop the line with the country definition — only frequency lines remain.
    frequency_lines = lines[1:]
    for line in frequency_lines:
        # Parse frequency range: strip whitespace, split on '@', drop leading '('.
        freq_section = line.strip().split("@")[0][1:]
        new_block_lower_point, new_block_upper_point = [
            float(freq.strip()) for freq in freq_section.split("-")
        ]
        # We can't use a frequency marked as NO-OFDM, so we must
        #  drop them. This includes channel 14 in JP and 12+13 in 00
        # NO-OFDM may overlap with the range on the previous line (strictly
        #  speaking it could be many lines of overlap, but let's ignore that
        #  given there's only 00 and JP with NO-OFDM and that doesn't go back
        #  more than one line.
        if ("NO-OFDM" in line):
            # If the NO-OFDM line encroaches on the top end of the previous
            #  block, redefine the top of the previous block to be where the
            #  NO-OFDM block starts
            if new_block_lower_point < block_upper_point:
                block_upper_point = new_block_lower_point
            continue

        # Commit the previous block before starting the new one.
        if block_lower_point and block_upper_point:
            freqency_blocks.append((block_lower_point, block_upper_point))

        # Remember this block for the next iteration's processing.
        block_lower_point = new_block_lower_point
        block_upper_point = new_block_upper_point

    if block_lower_point and block_upper_point:
        freqency_blocks.append((block_lower_point, block_upper_point))
    click.echo("Frequency blocks are {}" )
    pprint.pprint(freqency_blocks)
    return freqency_blocks


def flatten_frequency_blocks(blocks):
    """Merge adjacent or overlapping frequency ranges into contiguous blocks.

    regdb may list multiple overlapping ranges (e.g. different power limits for
    sub-ranges of the same band).  Since we only care about which channels are
    legal — not their power limits — we collapse overlapping ranges into a
    single (lower, upper) span.  Input is assumed sorted by increasing lower
    boundary, as regdbdump always outputs.

    Parameters
    ----------
    blocks : list of (float, float)
        Frequency blocks as returned by get_frequency_blocks_from_country_block().

    Returns
    -------
    list of (float, float)
        Merged, non-overlapping frequency ranges.
    """
    # We don't care for the power or band specific rules, so we can
    #  collapse the frequency ranges. regdb output is always sorted
    #  by increasing frequency, so we can make some assumptions about
    #  order as we're flattening
    flattened_blocks = []
    block_lower_point = 0
    block_upper_point = 0
    for start, end in blocks:
        if start > block_upper_point:
            # Gap between this block and the last — flush the accumulated block.
            if block_lower_point > 0 and block_upper_point > 0:
                flattened_blocks.append((block_lower_point, block_upper_point))
            block_lower_point = start
            block_upper_point = end
        else:
            # This block continues or is completely enclosed in the current block.
            if end > block_upper_point:
                # Extend the current block's upper boundary.
                block_upper_point = end

    flattened_blocks.append((block_lower_point, block_upper_point))
    return flattened_blocks


def get_channel_list_from_frequency_blocks(freq_list):
    """Return all 2.4 GHz channels whose full 20 MHz bandwidth fits inside freq_list.

    A channel is only included if its entire 20 MHz width (centre ± 10 MHz) is
    contained within at least one of the allowed frequency blocks.  This ensures
    we never advertise a channel whose edges fall outside the regulatory limit.

    Parameters
    ----------
    freq_list : list of (float, float)
        Flattened frequency ranges as returned by flatten_frequency_blocks().

    Returns
    -------
    list of int
        Channel numbers that are fully legal under the current regulatory domain.
    """
    allowed_channels = []
    for channel, centre_freq in channels.ISM_24_C2F.items():
        for start, end in freq_list:
            # Channel is legal only if the full 20 MHz band fits inside the block.
            if start <= centre_freq - (CHANNEL_WIDTH_MHZ_24 / 2) and \
                   centre_freq + (CHANNEL_WIDTH_MHZ_24 / 2) <= end:
                allowed_channels.append(channel)
    return allowed_channels


def channels_for_country(country_code):
    """Return the list of legal 2.4 GHz channel numbers for the given country code.

    Runs /sbin/regdbdump to decode the binary regulatory database, extracts the
    country's frequency rules, flattens overlapping ranges, and filters to
    channels whose full 20 MHz bandwidth fits within allowed frequencies.

    Parameters
    ----------
    country_code : str
        Two-letter ISO country code (e.g. 'US', 'DE').

    Returns
    -------
    list of int
        Legal channel numbers for this country, e.g. [1, 2, 3, ..., 11] for 'US'.
    """
    if os.path.exists("/sbin/regdbdump") and os.path.exists("/lib/crda/regulatory.bin"):
        regdump = subprocess.run(["/sbin/regdbdump", "/lib/crda/regulatory.bin"],
                                 stdout=subprocess.PIPE)
        lines = regdump.stdout.decode('utf-8').split("\n")
    else:
        # No crda (Debian 13 / Raspberry Pi OS Trixie): read wireless-regdb's
        #  regulatory.db, the file the kernel itself uses, in the same text form
        try:
            import regdb                         # cli.py puts this folder on sys.path
        except ImportError:
            from wifi_configurator import regdb  # imported as a package (tests)
        lines = regdb.dump_lines()
    country_rules_block = get_country_rules_block(country_code, lines)
    frequency_blocks = get_frequency_blocks_from_country_block(country_code, country_rules_block)
    frequency_blocks = flatten_frequency_blocks(frequency_blocks)
    return get_channel_list_from_frequency_blocks(frequency_blocks)
