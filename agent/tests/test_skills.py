from agent.skills import load_skills


def _make_fake_skills_dir(tmp_path):
    (tmp_path / "core").mkdir()
    (tmp_path / "market-data").mkdir()
    (tmp_path / "dca").mkdir()
    (tmp_path / "grid").mkdir()
    (tmp_path / "twap").mkdir()
    (tmp_path / "core" / "a.md").write_text("CORE_A")
    (tmp_path / "market-data" / "b.md").write_text("MD_B")
    (tmp_path / "dca" / "d.md").write_text("DCA_D")
    (tmp_path / "grid" / "g.md").write_text("GRID_G")
    (tmp_path / "twap" / "t.md").write_text("TWAP_T")
    return tmp_path


def test_grid_loads_core_and_market_data_and_grid_only(tmp_path):
    base = _make_fake_skills_dir(tmp_path)
    result = load_skills("GRID", base=base)
    assert "CORE_A" in result
    assert "MD_B" in result
    assert "GRID_G" in result
    assert "DCA_D" not in result
    assert "TWAP_T" not in result


def test_dca_loads_core_and_market_data_and_dca_only(tmp_path):
    base = _make_fake_skills_dir(tmp_path)
    result = load_skills("DCA", base=base)
    assert "CORE_A" in result
    assert "MD_B" in result
    assert "DCA_D" in result
    assert "GRID_G" not in result


def test_real_vendored_skills_wire_up_for_twap():
    result = load_skills("TWAP")
    assert "kraken-shared" in result  # core
    assert "kraken-market-intel" in result  # market data
    assert "kraken-twap-execution" in result  # the one matching strategy skill
    assert "kraken-dca-strategy" not in result
    assert "kraken-grid-trading" not in result
