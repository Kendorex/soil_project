//! Output selections used by the official standalone testcase.

use aquacrop_conformance::OfficialTestcase;
use aquacrop_core::io::{DailyOutputSection, ParticularOutputSection, read_output_requests};

#[test]
fn official_testcase_requests_all_daily_and_particular_results() -> Result<(), aquacrop_core::Error>
{
    let project = OfficialTestcase::new();
    let requests = read_output_requests(project.source_root())?;

    assert!(requests.aggregation.is_none());
    assert!(requests.daily.contains(&DailyOutputSection::WaterBalance));
    assert!(requests.daily.contains(&DailyOutputSection::PreparedCrop));
    assert!(requests.daily.contains(&DailyOutputSection::SoilWater));
    assert!(requests.daily.contains(&DailyOutputSection::Salinity));
    assert!(
        requests
            .daily
            .contains(&DailyOutputSection::CompartmentWater)
    );
    assert!(
        requests
            .daily
            .contains(&DailyOutputSection::CompartmentSalinity)
    );
    assert!(requests.daily.contains(&DailyOutputSection::Climate));
    assert!(!requests.daily.contains(&DailyOutputSection::Irrigation));
    assert!(
        requests
            .particular
            .contains(&ParticularOutputSection::Harvests)
    );
    assert!(
        requests
            .particular
            .contains(&ParticularOutputSection::Evaluation)
    );
    Ok(())
}
