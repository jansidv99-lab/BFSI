from agents.graph import _check_analysis


# ── pass cases ───────────────────────────────────────────────────────────────

def test_valid_analysis_passes():
    analysis = "The total realized PnL across all symbols was ₹12,450. NIFTY led with ₹8,200."
    valid, error = _check_analysis(analysis)
    assert valid is True
    assert error == ""


def test_single_digit_number_passes():
    analysis = "Only 3 trades were executed on this date, with an average price of 150."
    valid, error = _check_analysis(analysis)
    assert valid is True


def test_date_in_analysis_passes():
    # Dates contain numbers — should not cause a false failure
    analysis = "On 2026-05-19 there were 5 open positions with total exposure of ₹50,000."
    valid, error = _check_analysis(analysis)
    assert valid is True


def test_percentage_passes():
    analysis = "The unrealized loss was 8% of the total portfolio value of ₹1,00,000."
    valid, error = _check_analysis(analysis)
    assert valid is True


# ── fail: too short ───────────────────────────────────────────────────────────

def test_empty_string_fails():
    valid, error = _check_analysis("")
    assert valid is False
    assert "too short" in error


def test_short_string_fails():
    valid, error = _check_analysis("found 3")
    assert valid is False
    assert "too short" in error


def test_exactly_at_threshold_passes():
    # 30 chars with a number: should pass
    analysis = "Total brokerage charged was 400."
    assert len(analysis.strip()) >= 30
    valid, _ = _check_analysis(analysis)
    assert valid is True


def test_one_under_threshold_fails():
    # Build a 29-char string with a number
    analysis = "Brokerage charged was 400 rs."  # 29 chars
    assert len(analysis.strip()) < 30
    valid, error = _check_analysis(analysis)
    assert valid is False
    assert "too short" in error


# ── fail: refusal detected ────────────────────────────────────────────────────

def test_cannot_phrase_fails():
    analysis = "I cannot answer this question because the data is not available in the tables."
    valid, error = _check_analysis(analysis)
    assert valid is False
    assert "signals inability" in error


def test_no_data_phrase_fails():
    analysis = "There is no data matching your query for the selected date range of 30 days."
    valid, error = _check_analysis(analysis)
    assert valid is False
    assert "signals inability" in error


def test_sorry_phrase_fails():
    analysis = "Sorry, I was unable to find any records for the requested symbol in the database."
    valid, error = _check_analysis(analysis)
    assert valid is False
    assert "signals inability" in error


def test_unable_to_phrase_fails():
    analysis = "Unable to provide an analysis because the query returned 0 rows from the table."
    valid, error = _check_analysis(analysis)
    assert valid is False
    assert "signals inability" in error


def test_refusal_with_numbers_still_fails():
    # Old regex would pass this: has numbers AND length > 5
    analysis = "I cannot find trades for 2026-05-19. The date 2026-01-01 is also not present."
    valid, error = _check_analysis(analysis)
    assert valid is False
    assert "signals inability" in error


# ── fail: no numbers ─────────────────────────────────────────────────────────

def test_no_numbers_fails():
    analysis = "The portfolio had positions open across multiple symbols on the trade date selected."
    valid, error = _check_analysis(analysis)
    assert valid is False
    assert "no numbers" in error
