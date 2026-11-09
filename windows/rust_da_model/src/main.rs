use opc_da_lab_model::{LabModel, Quality, SourceTimestamp, TagId, TagValue};

fn main() {
    let mut model = LabModel::new();
    assert_eq!(
        model.snapshot(TagId::NumericDouble).quality,
        Quality::BadNoSample
    );

    model
        .publish(
            TagId::NumericDouble,
            TagValue::Double(12.5),
            SourceTimestamp::ValidFileTime(133_801_632_000_000_000),
        )
        .expect("numeric simulated sample");
    model
        .publish(
            TagId::Boolean,
            TagValue::Boolean(true),
            SourceTimestamp::ValidFileTime(133_801_632_000_000_007),
        )
        .expect("boolean simulated sample");

    println!(
        "{}: {:?}",
        TagId::NumericDouble.item_id(),
        model.snapshot(TagId::NumericDouble)
    );
    println!(
        "{}: {:?}",
        TagId::Boolean.item_id(),
        model.snapshot(TagId::Boolean)
    );

    model.set_source_available(false);
    assert_eq!(
        model.snapshot(TagId::NumericDouble).quality,
        Quality::BadNotConnected
    );
    assert_eq!(
        model.snapshot(TagId::Boolean).quality,
        Quality::BadNotConnected
    );
    println!("source disconnected: cached values are not Good");
}
