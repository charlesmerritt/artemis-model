"""Regional riparian buffer rules for LETO's vector split."""


def test_maine_rules_are_leto_v1s_verified_maine_configuration():
    # aauslander480/Leto LETO_v1_project/configs/maine_test.py "riparian": only perennial
    # streams, lakes and complex channels are buffered, at 45 ft.
    from pipeline.leto_riparian import MAINE_V1_RULES

    assert {c: d for c, (_, d) in MAINE_V1_RULES.flowline.items()} == {
        46000: 0.0, 46003: 0.0, 46006: 45.0, 46007: 0.0}
    assert {c: d for c, (_, d) in MAINE_V1_RULES.waterbody.items()} == {
        39000: 0.0, 39001: 0.0, 39005: 0.0, 39006: 0.0,
        39004: 45.0, 39009: 45.0, 39010: 45.0, 39011: 45.0, 39012: 45.0}
    assert {c: d for c, (_, d) in MAINE_V1_RULES.area.items()} == {
        46000: 0.0, 46003: 0.0, 46006: 45.0, 46007: 0.0, 53700: 45.0}
