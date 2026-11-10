import ovs_qos


def test_first_apply_creates_queues(fake_ovs):
    ovs_qos.apply_queues("s1-eth3", 10_000_000, {0: 7_000_000, 1: 3_000_000})
    assert fake_ovs("s1-eth3") == {0: 7_000_000, 1: 3_000_000}
    assert fake_ovs("s2-eth3") is None


def test_resize_updates_in_place(fake_ovs):
    ovs_qos.apply_queues("s1-eth3", 10_000_000, {0: 7_000_000, 1: 3_000_000})
    ovs_qos.apply_queues("s1-eth3", 10_000_000, {0: 5_000_000, 1: 5_000_000})
    assert fake_ovs("s1-eth3") == {0: 5_000_000, 1: 5_000_000}
    assert fake_ovs.queue_count() == 2      # no new rows: rates changed in place


def test_add_and_remove_queue(fake_ovs):
    ovs_qos.apply_queues("s1-eth3", 10_000_000, {0: 7_000_000, 1: 3_000_000})
    ovs_qos.apply_queues("s1-eth3", 10_000_000, {0: 6_000_000, 1: 3_000_000, 2: 1_000_000})
    assert fake_ovs("s1-eth3") == {0: 6_000_000, 1: 3_000_000, 2: 1_000_000}
    ovs_qos.apply_queues("s1-eth3", 10_000_000, {0: 6_000_000, 2: 1_000_000})
    assert fake_ovs("s1-eth3") == {0: 6_000_000, 2: 1_000_000}
    assert fake_ovs.queue_count() == 2      # removed queue was destroyed


def test_port_checks(fake_ovs):
    assert ovs_qos.port_exists("s1-eth3")
    assert not ovs_qos.port_exists("s9-eth9")
    assert not ovs_qos.has_qos("s1-eth3")
    ovs_qos.apply_queues("s1-eth3", 10_000_000, {0: 1_000_000})
    assert ovs_qos.has_qos("s1-eth3")


def test_clear_all(fake_ovs):
    ovs_qos.apply_queues("s1-eth3", 10_000_000, {0: 7_000_000})
    ovs_qos.clear_all()
    assert fake_ovs("s1-eth3") is None and fake_ovs.queue_count() == 0
