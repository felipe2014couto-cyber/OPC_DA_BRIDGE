//! Deterministic laboratory source kept outside the COM adapter.

use super::{CoreError, ServerCore, SourceTimestamp, TagId, TagValue};

/// Generates a changing numeric and boolean sample for local laboratory tests.
/// The caller supplies source time explicitly so tests can exercise absent and
/// invalid timestamp rules without reading the wall clock.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct LabSimulator {
    sequence: u64,
    available: bool,
}

impl Default for LabSimulator {
    fn default() -> Self {
        Self::new()
    }
}

impl LabSimulator {
    pub const fn new() -> Self {
        Self {
            sequence: 0,
            available: true,
        }
    }

    pub const fn is_available(self) -> bool {
        self.available
    }

    pub const fn sequence(self) -> u64 {
        self.sequence
    }

    pub fn set_available(&mut self, core: &mut ServerCore, available: bool) {
        self.available = available;
        core.set_source_available(available);
    }

    /// Publishes one sample for each laboratory item. This is an in-process
    /// simulator, not an OPC DA server and not a history/replay mechanism.
    pub fn publish_next(
        &mut self,
        core: &mut ServerCore,
        timestamp: SourceTimestamp,
    ) -> Result<(), CoreError> {
        if !self.available {
            return Err(CoreError::SourceUnavailable);
        }

        let next_sequence = self
            .sequence
            .checked_add(1)
            .ok_or(CoreError::SimulationSequenceExhausted)?;
        let numeric = 12.5 + self.sequence as f64;
        let boolean = self.sequence % 2 == 0;

        core.publish(TagId::NumericDouble, TagValue::Double(numeric), timestamp)?;
        core.publish(TagId::Boolean, TagValue::Boolean(boolean), timestamp)?;
        self.sequence = next_sequence;
        Ok(())
    }
}
