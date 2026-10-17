def test_the_daily_dataset_names_the_us_equity_calendar() -> None:
    """v24's calendar_id (the plan's O10), on this plugin's move to v25: the
    daily closes' business dates follow the platform's US equity venues
    calendar."""
    from alpaca.declared import CATALOGUE, US_EQUITY_CALENDAR

    assert US_EQUITY_CALENDAR == "CAL-01M4VF0F4XGP3RATCCSKDPZSBP"
    (daily,) = [dataset for dataset in CATALOGUE if dataset.key == "daily"]
    assert daily.calendar_id == US_EQUITY_CALENDAR
