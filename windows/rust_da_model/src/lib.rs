//! OPC-independent data model for the Rust DA lab proof of concept.
//!
//! This crate contains no Windows, COM, OPC Foundation, or third-party code.

mod server_core;
pub use server_core::{CoreError, DataChange, GroupHandle, ItemHandle, ItemRead, ServerCore};
mod simulator;
pub use simulator::LabSimulator;

pub const NUMERIC_ITEM_ID: &str = "RustLab.Numeric.Double";
pub const BOOLEAN_ITEM_ID: &str = "RustLab.Boolean";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum TagId {
    NumericDouble,
    Boolean,
}

impl TagId {
    pub const fn item_id(self) -> &'static str {
        match self {
            Self::NumericDouble => NUMERIC_ITEM_ID,
            Self::Boolean => BOOLEAN_ITEM_ID,
        }
    }

    const fn expected_kind(self) -> ValueKind {
        match self {
            Self::NumericDouble => ValueKind::Double,
            Self::Boolean => ValueKind::Boolean,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ValueKind {
    Double,
    Boolean,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum TagValue {
    Double(f64),
    Boolean(bool),
}

impl TagValue {
    const fn kind(self) -> ValueKind {
        match self {
            Self::Double(_) => ValueKind::Double,
            Self::Boolean(_) => ValueKind::Boolean,
        }
    }
}

/// Source FILETIME input. `Valid` stores the original 100 ns ticks since
/// 1601-01-01 UTC; conversion/validation failures must be passed as `Invalid`.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum SourceTimestamp {
    ValidFileTime(u64),
    Missing,
    Invalid,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Quality {
    /// No accepted source sample has arrived yet.
    BadNoSample,
    /// A valid source value and nonzero source FILETIME were received.
    Good,
    /// The value was received, but the source timestamp was absent.
    UncertainTimestampMissing,
    /// The value was received, but the source timestamp was invalid.
    UncertainTimestampInvalid,
    /// The source disconnected. Any retained value is explicitly stale.
    BadNotConnected,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct TagSnapshot {
    pub value: Option<TagValue>,
    pub quality: Quality,
    pub source_timestamp_filetime: Option<u64>,
}

impl TagSnapshot {
    const fn initial() -> Self {
        Self {
            value: None,
            quality: Quality::BadNoSample,
            source_timestamp_filetime: None,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ModelError {
    WrongType {
        expected: ValueKind,
        actual: ValueKind,
    },
    SourceUnavailable,
    WriteRejectedReadOnly,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct LabModel {
    source_available: bool,
    numeric: TagSnapshot,
    boolean: TagSnapshot,
}

impl Default for LabModel {
    fn default() -> Self {
        Self::new()
    }
}

impl LabModel {
    /// Starts with the simulated source available but with no Good samples.
    pub const fn new() -> Self {
        Self {
            source_available: true,
            numeric: TagSnapshot::initial(),
            boolean: TagSnapshot::initial(),
        }
    }

    pub const fn snapshot(&self, tag: TagId) -> TagSnapshot {
        match tag {
            TagId::NumericDouble => self.numeric,
            TagId::Boolean => self.boolean,
        }
    }

    pub fn publish(
        &mut self,
        tag: TagId,
        value: TagValue,
        timestamp: SourceTimestamp,
    ) -> Result<(), ModelError> {
        if !self.source_available {
            return Err(ModelError::SourceUnavailable);
        }

        let expected = tag.expected_kind();
        let actual = value.kind();
        if expected != actual {
            return Err(ModelError::WrongType { expected, actual });
        }

        let (source_timestamp_filetime, quality) = match timestamp {
            SourceTimestamp::ValidFileTime(ticks) if ticks != 0 => (Some(ticks), Quality::Good),
            SourceTimestamp::ValidFileTime(_) | SourceTimestamp::Invalid => {
                (None, Quality::UncertainTimestampInvalid)
            }
            SourceTimestamp::Missing => (None, Quality::UncertainTimestampMissing),
        };
        let snapshot = TagSnapshot {
            value: Some(value),
            quality,
            source_timestamp_filetime,
        };
        match tag {
            TagId::NumericDouble => self.numeric = snapshot,
            TagId::Boolean => self.boolean = snapshot,
        }
        Ok(())
    }

    /// Connection loss degrades the cached snapshot and never leaves it Good.
    pub fn set_source_available(&mut self, available: bool) {
        self.source_available = available;
        if !available {
            self.numeric.quality = Quality::BadNotConnected;
            self.boolean.quality = Quality::BadNotConnected;
        }
        // Reconnection does not promote cached values. A new valid sample is
        // required before either item can become Good again.
    }

    /// The lab model has no write path, regardless of item or input value.
    pub fn write(&mut self, _item_id: &str, _value: TagValue) -> Result<(), ModelError> {
        Err(ModelError::WriteRejectedReadOnly)
    }

    pub const fn source_available(&self) -> bool {
        self.source_available
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const FILETIME_SAMPLE: u64 = 133_801_632_000_000_000;

    #[test]
    fn both_lab_items_start_without_good_data() {
        let model = LabModel::new();
        for tag in [TagId::NumericDouble, TagId::Boolean] {
            assert_eq!(model.snapshot(tag).value, None);
            assert_eq!(model.snapshot(tag).quality, Quality::BadNoSample);
            assert_eq!(model.snapshot(tag).source_timestamp_filetime, None);
        }
        assert_eq!(TagId::NumericDouble.item_id(), "RustLab.Numeric.Double");
        assert_eq!(TagId::Boolean.item_id(), "RustLab.Boolean");
    }

    #[test]
    fn valid_samples_preserve_type_value_and_source_filetime() {
        let mut model = LabModel::new();
        model
            .publish(
                TagId::NumericDouble,
                TagValue::Double(12.5),
                SourceTimestamp::ValidFileTime(FILETIME_SAMPLE),
            )
            .unwrap();
        model
            .publish(
                TagId::Boolean,
                TagValue::Boolean(true),
                SourceTimestamp::ValidFileTime(FILETIME_SAMPLE + 7),
            )
            .unwrap();

        assert_eq!(
            model.snapshot(TagId::NumericDouble),
            TagSnapshot {
                value: Some(TagValue::Double(12.5)),
                quality: Quality::Good,
                source_timestamp_filetime: Some(FILETIME_SAMPLE),
            }
        );
        assert_eq!(
            model.snapshot(TagId::Boolean).value,
            Some(TagValue::Boolean(true))
        );
        assert_eq!(model.snapshot(TagId::Boolean).quality, Quality::Good);
    }

    #[test]
    fn missing_or_invalid_timestamps_are_not_replaced_with_clock_time() {
        let mut model = LabModel::new();
        model
            .publish(
                TagId::NumericDouble,
                TagValue::Double(1.0),
                SourceTimestamp::Missing,
            )
            .unwrap();
        let missing = model.snapshot(TagId::NumericDouble);
        assert_eq!(missing.value, Some(TagValue::Double(1.0)));
        assert_eq!(missing.quality, Quality::UncertainTimestampMissing);
        assert_eq!(missing.source_timestamp_filetime, None);

        model
            .publish(
                TagId::NumericDouble,
                TagValue::Double(2.0),
                SourceTimestamp::ValidFileTime(0),
            )
            .unwrap();
        let invalid = model.snapshot(TagId::NumericDouble);
        assert_eq!(invalid.value, Some(TagValue::Double(2.0)));
        assert_eq!(invalid.quality, Quality::UncertainTimestampInvalid);
        assert_eq!(invalid.source_timestamp_filetime, None);
    }

    #[test]
    fn explicit_timestamp_conversion_failure_stays_uncertain() {
        let mut model = LabModel::new();
        model
            .publish(
                TagId::Boolean,
                TagValue::Boolean(false),
                SourceTimestamp::Invalid,
            )
            .unwrap();
        let snapshot = model.snapshot(TagId::Boolean);
        assert_eq!(snapshot.quality, Quality::UncertainTimestampInvalid);
        assert_eq!(snapshot.source_timestamp_filetime, None);
    }

    #[test]
    fn wrong_value_type_does_not_change_the_snapshot() {
        let mut model = LabModel::new();
        let before = model.snapshot(TagId::Boolean);
        assert_eq!(
            model.publish(
                TagId::Boolean,
                TagValue::Double(5.0),
                SourceTimestamp::ValidFileTime(FILETIME_SAMPLE),
            ),
            Err(ModelError::WrongType {
                expected: ValueKind::Boolean,
                actual: ValueKind::Double,
            })
        );
        assert_eq!(model.snapshot(TagId::Boolean), before);
    }

    #[test]
    fn disconnection_and_reconnection_never_restore_stale_good_quality() {
        let mut model = LabModel::new();
        model
            .publish(
                TagId::NumericDouble,
                TagValue::Double(42.0),
                SourceTimestamp::ValidFileTime(FILETIME_SAMPLE),
            )
            .unwrap();
        model.set_source_available(false);
        let disconnected = model.snapshot(TagId::NumericDouble);
        assert_eq!(disconnected.value, Some(TagValue::Double(42.0)));
        assert_eq!(disconnected.quality, Quality::BadNotConnected);
        assert_eq!(
            disconnected.source_timestamp_filetime,
            Some(FILETIME_SAMPLE)
        );
        assert_eq!(
            model.publish(
                TagId::Boolean,
                TagValue::Boolean(false),
                SourceTimestamp::ValidFileTime(FILETIME_SAMPLE),
            ),
            Err(ModelError::SourceUnavailable)
        );

        model.set_source_available(true);
        assert_eq!(
            model.snapshot(TagId::NumericDouble).quality,
            Quality::BadNotConnected
        );
        model
            .publish(
                TagId::NumericDouble,
                TagValue::Double(43.0),
                SourceTimestamp::Missing,
            )
            .unwrap();
        assert_eq!(
            model.snapshot(TagId::NumericDouble).quality,
            Quality::UncertainTimestampMissing
        );
    }

    #[test]
    fn writes_are_rejected_without_mutating_any_item() {
        let mut model = LabModel::new();
        let before_numeric = model.snapshot(TagId::NumericDouble);
        let before_boolean = model.snapshot(TagId::Boolean);
        assert_eq!(
            model.write(TagId::NumericDouble.item_id(), TagValue::Double(99.0)),
            Err(ModelError::WriteRejectedReadOnly)
        );
        assert_eq!(
            model.write(TagId::Boolean.item_id(), TagValue::Boolean(true)),
            Err(ModelError::WriteRejectedReadOnly)
        );
        assert_eq!(model.snapshot(TagId::NumericDouble), before_numeric);
        assert_eq!(model.snapshot(TagId::Boolean), before_boolean);
    }

    #[test]
    fn service_core_browses_groups_adds_items_reads_and_removes_them() {
        let mut core = ServerCore::new();
        assert_eq!(core.browse(), [NUMERIC_ITEM_ID, BOOLEAN_ITEM_ID]);
        let group = core.add_group("laboratory", true, 0).unwrap();
        assert_eq!(core.group_update_rate(group), Ok(1));
        assert_eq!(
            core.add_group("laboratory", true, 100),
            Err(CoreError::GroupNameInUse)
        );
        assert_eq!(
            core.add_item(group, "missing.item", 7),
            Err(CoreError::UnknownItemId)
        );
        let numeric = core.add_item(group, NUMERIC_ITEM_ID, 77).unwrap();
        let boolean = core.add_item(group, BOOLEAN_ITEM_ID, 88).unwrap();
        core.publish(
            TagId::NumericDouble,
            TagValue::Double(4.25),
            SourceTimestamp::ValidFileTime(FILETIME_SAMPLE),
        )
        .unwrap();

        let reads = core.read(group, &[numeric, boolean]).unwrap();
        assert_eq!(reads[0].client_handle, 77);
        assert_eq!(reads[0].item_id, NUMERIC_ITEM_ID);
        assert_eq!(
            reads[0].result.as_ref().unwrap().value,
            Some(TagValue::Double(4.25))
        );
        assert_eq!(
            reads[1].result.as_ref().unwrap().quality,
            Quality::BadNoSample
        );
        assert_eq!(
            core.write(group, numeric, TagValue::Double(10.0)),
            Err(CoreError::WriteRejectedReadOnly)
        );
        core.remove_item(group, boolean).unwrap();
        assert_eq!(
            core.read(group, &[boolean]).unwrap()[0].result,
            Err(CoreError::ItemNotFound)
        );
        assert_eq!(core.remove_group(group), Ok(()));
        assert_eq!(core.read(group, &[]), Err(CoreError::GroupNotFound));
    }

    #[test]
    fn service_core_notifies_updates_and_degrades_on_source_loss() {
        let mut core = ServerCore::new();
        let group = core.add_group("subscription", true, 100).unwrap();
        let numeric = core.add_item(group, NUMERIC_ITEM_ID, 123).unwrap();
        let receiver = core.subscribe(group).unwrap();
        let initial = receiver.try_recv().unwrap();
        assert_eq!(initial.len(), 1);
        assert_eq!(initial[0].item_handle, numeric);
        assert_eq!(initial[0].client_handle, 123);
        assert_eq!(initial[0].snapshot.quality, Quality::BadNoSample);

        core.publish(
            TagId::NumericDouble,
            TagValue::Double(9.5),
            SourceTimestamp::ValidFileTime(FILETIME_SAMPLE),
        )
        .unwrap();
        let good = receiver.try_recv().unwrap();
        assert_eq!(good[0].snapshot.value, Some(TagValue::Double(9.5)));
        assert_eq!(good[0].snapshot.quality, Quality::Good);
        assert_eq!(
            good[0].snapshot.source_timestamp_filetime,
            Some(FILETIME_SAMPLE)
        );

        core.set_source_available(false);
        let bad = receiver.try_recv().unwrap();
        assert_eq!(bad[0].snapshot.quality, Quality::BadNotConnected);
        core.set_source_available(true);
        assert!(receiver.try_recv().is_err());
        core.publish(
            TagId::NumericDouble,
            TagValue::Double(10.0),
            SourceTimestamp::ValidFileTime(FILETIME_SAMPLE + 1),
        )
        .unwrap();
        assert_eq!(
            receiver.try_recv().unwrap()[0].snapshot.quality,
            Quality::Good
        );
    }

    #[test]
    fn inactive_groups_do_not_emit_notifications() {
        let mut core = ServerCore::new();
        let group = core.add_group("inactive", false, 100).unwrap();
        core.add_item(group, BOOLEAN_ITEM_ID, 1).unwrap();
        let receiver = core.subscribe(group).unwrap();
        core.publish(
            TagId::Boolean,
            TagValue::Boolean(true),
            SourceTimestamp::ValidFileTime(FILETIME_SAMPLE),
        )
        .unwrap();
        assert!(receiver.try_recv().is_err());
        core.set_group_active(group, true).unwrap();
        core.publish(
            TagId::Boolean,
            TagValue::Boolean(false),
            SourceTimestamp::ValidFileTime(FILETIME_SAMPLE + 1),
        )
        .unwrap();
        assert_eq!(
            receiver.try_recv().unwrap()[0].snapshot.value,
            Some(TagValue::Boolean(false))
        );
    }

    #[test]
    fn simulator_changes_values_and_only_recovers_after_a_fresh_sample() {
        let mut core = ServerCore::new();
        let group = core.add_group("simulated-source", true, 100).unwrap();
        let numeric = core.add_item(group, NUMERIC_ITEM_ID, 1).unwrap();
        let boolean = core.add_item(group, BOOLEAN_ITEM_ID, 2).unwrap();
        let mut simulator = LabSimulator::new();

        simulator
            .publish_next(&mut core, SourceTimestamp::ValidFileTime(FILETIME_SAMPLE))
            .unwrap();
        let first = core.read(group, &[numeric, boolean]).unwrap();
        assert_eq!(
            first[0].result.as_ref().unwrap().value,
            Some(TagValue::Double(12.5))
        );
        assert_eq!(
            first[1].result.as_ref().unwrap().value,
            Some(TagValue::Boolean(true))
        );

        simulator.set_available(&mut core, false);
        let disconnected = core.read(group, &[numeric, boolean]).unwrap();
        assert!(disconnected
            .iter()
            .all(|result| result.result.as_ref().unwrap().quality == Quality::BadNotConnected));
        assert_eq!(
            simulator.publish_next(
                &mut core,
                SourceTimestamp::ValidFileTime(FILETIME_SAMPLE + 1)
            ),
            Err(CoreError::SourceUnavailable)
        );

        simulator.set_available(&mut core, true);
        let still_stale = core.read(group, &[numeric, boolean]).unwrap();
        assert!(still_stale
            .iter()
            .all(|result| result.result.as_ref().unwrap().quality == Quality::BadNotConnected));
        simulator
            .publish_next(
                &mut core,
                SourceTimestamp::ValidFileTime(FILETIME_SAMPLE + 1),
            )
            .unwrap();
        let recovered = core.read(group, &[numeric, boolean]).unwrap();
        assert!(recovered
            .iter()
            .all(|result| result.result.as_ref().unwrap().quality == Quality::Good));
        assert_eq!(
            recovered[0].result.as_ref().unwrap().value,
            Some(TagValue::Double(13.5))
        );
    }
}
