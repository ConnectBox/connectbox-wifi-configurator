#!/usr/bin/env python
# -*- coding: utf-8 -*-

from wifi_configurator import scan


def freq_signal_dict_as_scan_output(cs_dict):
    scan_output = ""
    for freq, signal in cs_dict.items():
        scan_output = "%sBSS: blah\nfreq: %s\nsignal: %s\n" % \
                      (scan_output, freq, signal)
    return scan_output


def test_get_country_count_populated(iw_dev_scan_0):
    c = scan.get_country_count_from_iw_output(iw_dev_scan_0)
    assert c.most_common(1)[0][0] == "TR"
    assert len(list(c.elements())) == 4
    assert c["AL"] == 1


def test_get_country_count_unpopulated(iw_dev_scan_1):
    c = scan.get_country_count_from_iw_output(iw_dev_scan_1)
    assert c.most_common(1) == []
    assert not list(c.elements())


def test_get_country_count_empty(iw_dev_scan_2):
    c = scan.get_country_count_from_iw_output(iw_dev_scan_2)
    assert c.most_common(1) == []
    assert not list(c.elements())


def test_get_country_count_populated2(iw_dev_scan_3):
    c = scan.get_country_count_from_iw_output(iw_dev_scan_3)
    assert c.most_common(1)[0][0] == "AU"
    assert len(list(c.elements())) == 2


AU_REGDB = """\
country AU: DFS-ETSI
	(2402.000 - 2482.000 @ 40.000), (20.00), (N/A)
	(5170.000 - 5250.000 @ 80.000), (17.00), (N/A), AUTO-BW
	(5250.000 - 5330.000 @ 80.000), (24.00), (N/A), DFS, AUTO-BW
	(5490.000 - 5710.000 @ 160.000), (24.00), (N/A), DFS
	(5735.000 - 5835.000 @ 80.000), (30.00), (N/A)"""


def test_get_country_rules_block_matching(regdb_lines):
    block_lines = scan.get_country_rules_block("AU", regdb_lines)
    assert block_lines == AU_REGDB.split("\n")


def test_au_freq_extraction(regdb_lines):
    block_lines = scan.get_country_rules_block("AU", regdb_lines)
    freq_blocks = scan.get_frequency_blocks_from_country_block(block_lines)
    assert freq_blocks == [
        (2402, 2482),
        (5170, 5250),
        (5250, 5330),
        (5490, 5710),
        (5735, 5835),
    ]


def test_flattening_of_au_freqs(regdb_lines):
    block_lines = scan.get_country_rules_block("AU", regdb_lines)
    freq_blocks = scan.get_frequency_blocks_from_country_block(block_lines)
    freq_blocks = scan.flatten_frequency_blocks(freq_blocks)
    assert scan.flatten_frequency_blocks(freq_blocks) == [
        (2402, 2482),
        (5170, 5330),
        (5490, 5710),
        (5735, 5835),
    ]


def test_channel_list_au(regdb_lines):
    block_lines = scan.get_country_rules_block("AU", regdb_lines)
    freq_blocks = scan.get_frequency_blocks_from_country_block(block_lines)
    freq_blocks = scan.flatten_frequency_blocks(freq_blocks)
    assert scan.get_channel_list_from_frequency_blocks(freq_blocks) == \
        [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]


UNSET_REGDB = """\
country 00: DFS-UNSET
	(2402.000 - 2472.000 @ 40.000), (20.00), (N/A)
	(2457.000 - 2482.000 @ 20.000), (20.00), (N/A), NO-IR, AUTO-BW
	(2474.000 - 2494.000 @ 20.000), (20.00), (N/A), NO-OFDM, NO-IR
	(5170.000 - 5250.000 @ 80.000), (20.00), (N/A), NO-IR, AUTO-BW
	(5250.000 - 5330.000 @ 80.000), (20.00), (N/A), DFS, NO-IR, AUTO-BW
	(5490.000 - 5730.000 @ 160.000), (20.00), (N/A), DFS, NO-IR
	(5735.000 - 5835.000 @ 80.000), (20.00), (N/A), NO-IR
	(57240.000 - 63720.000 @ 2160.000), (N/A), (N/A)"""


def test_get_country_rules_block_first(regdb_lines):
    block_lines = scan.get_country_rules_block("00", regdb_lines)
    assert block_lines == UNSET_REGDB.split("\n")


def test_unset_freq_extraction(regdb_lines):
    block_lines = scan.get_country_rules_block("00", regdb_lines)
    freq_blocks = scan.get_frequency_blocks_from_country_block(block_lines)
    assert freq_blocks == [
        (2402, 2472),
        (2457, 2474),
        (5170, 5250),
        (5250, 5330),
        (5490, 5730),
        (5735, 5835),
        (57240, 63720),
    ]


def test_flattening_of_unset_freqs(regdb_lines):
    block_lines = scan.get_country_rules_block("00", regdb_lines)
    freq_blocks = scan.get_frequency_blocks_from_country_block(block_lines)
    assert scan.flatten_frequency_blocks(freq_blocks) == [
        (2402, 2474),
        (5170, 5330),
        (5490, 5730),
        (5735, 5835),
        (57240, 63720),
    ]


def test_channel_list_unset(regdb_lines):
    block_lines = scan.get_country_rules_block("00", regdb_lines)
    freq_blocks = scan.get_frequency_blocks_from_country_block(block_lines)
    freq_blocks = scan.flatten_frequency_blocks(freq_blocks)
    assert scan.get_channel_list_from_frequency_blocks(freq_blocks) == \
        [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]


JP_REGDB = """\
country JP: DFS-JP
	(2402.000 - 2482.000 @ 40.000), (20.00), (N/A)
	(2474.000 - 2494.000 @ 20.000), (20.00), (N/A), NO-OFDM
	(4910.000 - 4990.000 @ 40.000), (23.00), (N/A)
	(5030.000 - 5090.000 @ 40.000), (23.00), (N/A)
	(5170.000 - 5250.000 @ 80.000), (20.00), (N/A), AUTO-BW
	(5250.000 - 5330.000 @ 80.000), (20.00), (N/A), DFS, AUTO-BW
	(5490.000 - 5710.000 @ 160.000), (23.00), (N/A), DFS
	(59000.000 - 66000.000 @ 2160.000), (10.00), (N/A)"""


def test_get_country_rules_block_jp(regdb_lines):
    block_lines = scan.get_country_rules_block("JP", regdb_lines)
    assert block_lines == JP_REGDB.split("\n")


def test_jp_freq_extraction(regdb_lines):
    block_lines = scan.get_country_rules_block("JP", regdb_lines)
    freq_blocks = scan.get_frequency_blocks_from_country_block(block_lines)
    assert freq_blocks == [
        (2402, 2474),
        (4910, 4990),
        (5030, 5090),
        (5170, 5250),
        (5250, 5330),
        (5490, 5710),
        (59000, 66000),
    ]


def test_flattening_of_jp_freqs(regdb_lines):
    block_lines = scan.get_country_rules_block("JP", regdb_lines)
    freq_blocks = scan.get_frequency_blocks_from_country_block(block_lines)
    assert scan.flatten_frequency_blocks(freq_blocks) == [
        (2402, 2474),
        (4910, 4990),
        (5030, 5090),
        (5170, 5330),
        (5490, 5710),
        (59000, 66000),
    ]


def test_get_country_rules_block_unmatched(regdb_lines):
    block_lines = scan.get_country_rules_block("NOMATCH", regdb_lines)
    assert not block_lines


def test_get_freq_signals_0(iw_dev_scan_0):
    assert scan.get_freq_signal_tuples_from_iw_output(iw_dev_scan_0) == [
        (2412, -48.0),
        (2432, -84.0),
        (2442, -42.0),
        (2442, -83.0),
        (2442, -82.0),
        (2462, -85.0),
        (5180, -89.0),
        (2412, -84.0),
        (2452, -88.0),
    ]


def test_get_freq_signals_1(iw_dev_scan_1):
    assert scan.get_freq_signal_tuples_from_iw_output(iw_dev_scan_1) == [
        (2412, -48.0),
    ]


def test_get_freq_signals_2(iw_dev_scan_2):
    assert scan.get_freq_signal_tuples_from_iw_output(iw_dev_scan_2) == []


def test_get_freq_signals_3(iw_dev_scan_3):
    assert scan.get_freq_signal_tuples_from_iw_output(iw_dev_scan_3) == [
        (2412, -32.0),
        (2442, -36.0),
        (5180, -25.0),
    ]


def test_overlap_empty_full():
    assert not scan.channel_overlaps_with_others(1, [])
    assert scan.channel_overlaps_with_others(
        1,
        [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]
    )


def test_overlap_end_spectrum():
    assert not scan.channel_overlaps_with_others(
        1,
        [5, 6, 7, 8, 9, 10, 11, 12, 13]
    )
    assert scan.channel_overlaps_with_others(1, [4])
    assert not scan.channel_overlaps_with_others(
        13,
        [1, 2, 3, 4, 5, 6, 7, 8, 9]
    )
    assert scan.channel_overlaps_with_others(13, [10])


def test_overlap_mid_spectrum():
    assert not scan.channel_overlaps_with_others(
        5,
        [1, 9, 10, 11, 12, 13]
    )
    assert scan.channel_overlaps_with_others(
        5,
        [1, 2, 9, 10, 11, 12, 13]
    )
    assert scan.channel_overlaps_with_others(
        5,
        [1, 8, 9, 10, 11, 12, 13]
    )
    assert not scan.channel_overlaps_with_others(
        9,
        [1, 2, 3, 4, 5, 13]
    )
    assert scan.channel_overlaps_with_others(
        9,
        [1, 2, 3, 4, 5, 6, 13]
    )
    assert scan.channel_overlaps_with_others(
        9,
        [1, 2, 3, 4, 5, 12, 13]
    )
    assert not scan.channel_overlaps_with_others(
        9,
        [5, 13]
    )
    assert scan.channel_overlaps_with_others(
        9,
        [6]
    )
    assert scan.channel_overlaps_with_others(
        9,
        [12]
    )


def test_uncontested_channels():
    # Channel choice by interference score (2026-11-06): overlap with each
    #  network heard (1, 3/4, 1/2, 1/4 for 0-3 channels apart) times its power.
    # 3, 9 and 13 busy: 1 (1/2 from 3) and 6 (1/4 from 3 and 9) tie; 1 wins.
    assert scan.get_available_uncontested_channel(
        range(1, 14),
        freq_signal_dict_as_scan_output(
            {2422: -50, 2452: -50, 2472: -50}
        )
    ) == 1
    # 3 and 9 busy: 13 is four channels from 9, nothing overlaps it.
    assert scan.get_available_uncontested_channel(
        range(1, 14),
        freq_signal_dict_as_scan_output({2422: -50, 2452: -50})
    ) == 13


def test_channel_between_busy_neighbours_not_chosen():
    # Kirk's room 2026-11-06: 2, 8 and 10 busy; old code could pick 9.
    out = freq_signal_dict_as_scan_output({2417: -40, 2447: -55, 2457: -50})
    assert scan.get_available_uncontested_channel(range(1, 12), out) == 6
    assert scan.get_available_uncontested_channel(range(1, 12), out, 9) == 6


def test_current_channel_kept_unless_clearly_worse():
    out = freq_signal_dict_as_scan_output({2412: -60})
    # 10 and 11 both score 0: keep 10
    assert scan.get_available_uncontested_channel(range(1, 12), out, 10) == 10
    scores = {c: 1.0 for c in range(1, 12)}
    assert scan.choose_channel(range(1, 12), {**scores, 6: 0.6, 7: 1.1}, 7) == 7
    assert scan.choose_channel(range(1, 12), {**scores, 6: 0.4}, 7) == 6


def test_empty_scan_keeps_current_or_prefers_1_6_11():
    assert scan.get_available_uncontested_channel(range(1, 12), "", 9) == 9
    assert scan.get_available_uncontested_channel(range(1, 12), "") == 1
    assert scan.get_available_uncontested_channel(range(1, 12), "", 13) == 1
    assert scan.get_available_uncontested_channel([], "", 9) == scan.NO_CHANNEL


def test_overlap_fraction():
    assert [scan.overlap_fraction(6, 6 + d) for d in range(5)] == \
        [1.0, 0.75, 0.5, 0.25, 0.0]


def test_max_signal_at_each_freq_returns_map():
    assert scan.get_max_signal_at_each_freq(
        [(2412, -50.0), (2412, -40.0), (2437, -70.0)]) == {2412: -40.0, 2437: -70.0}
