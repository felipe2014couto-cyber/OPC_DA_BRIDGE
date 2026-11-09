//! Protocol-neutral service core kept separate from any OPC/COM binding.

use super::{
    LabModel, ModelError, SourceTimestamp, TagId, TagSnapshot, TagValue, ValueKind,
    BOOLEAN_ITEM_ID, NUMERIC_ITEM_ID,
};

/// Errors from the protocol-neutral DA-shaped service core.
///
/// These are deliberately not HRESULT values. A future, separately reviewed
/// COM adapter must map them to the OPC DA-defined result codes.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CoreError {
    UnknownItemId,
    GroupNotFound,
    ItemNotFound,
    GroupNameInUse,
    HandleSpaceExhausted,
    SimulationSequenceExhausted,
    SourceUnavailable,
    WriteRejectedReadOnly,
    WrongType {
        expected: ValueKind,
        actual: ValueKind,
    },
}

impl From<ModelError> for CoreError {
    fn from(error: ModelError) -> Self {
        match error {
            ModelError::WrongType { expected, actual } => Self::WrongType { expected, actual },
            ModelError::SourceUnavailable => Self::SourceUnavailable,
            ModelError::WriteRejectedReadOnly => Self::WriteRejectedReadOnly,
        }
    }
}

/// Opaque in-process handle. A COM adapter must never expose it as a pointer.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct GroupHandle(u32);

impl GroupHandle {
    pub const fn get(self) -> u32 {
        self.0
    }
}

/// Opaque in-process item handle.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ItemHandle(u32);

impl ItemHandle {
    pub const fn get(self) -> u32 {
        self.0
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct ItemRead {
    pub item_handle: ItemHandle,
    pub client_handle: u32,
    pub item_id: &'static str,
    pub result: Result<TagSnapshot, CoreError>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct DataChange {
    pub item_handle: ItemHandle,
    pub client_handle: u32,
    pub item_id: &'static str,
    pub snapshot: TagSnapshot,
}

struct GroupItem {
    tag: TagId,
    client_handle: u32,
}

struct Group {
    name: String,
    active: bool,
    update_rate_ms: u32,
    next_item_handle: u32,
    items: std::collections::BTreeMap<ItemHandle, GroupItem>,
    subscribers: Vec<std::sync::mpsc::Sender<Vec<DataChange>>>,
}

/// Protocol-neutral group, item, read, and notification core for a later COM
/// adapter. It has no COM ABI, registration, browse-interface, or HRESULT code.
pub struct ServerCore {
    model: LabModel,
    next_group_handle: u32,
    groups: std::collections::BTreeMap<GroupHandle, Group>,
}

impl Default for ServerCore {
    fn default() -> Self {
        Self::new()
    }
}

impl ServerCore {
    pub const fn new() -> Self {
        Self {
            model: LabModel::new(),
            next_group_handle: 1,
            groups: std::collections::BTreeMap::new(),
        }
    }

    pub fn browse(&self) -> [&'static str; 2] {
        [NUMERIC_ITEM_ID, BOOLEAN_ITEM_ID]
    }

    pub fn add_group(
        &mut self,
        name: impl Into<String>,
        active: bool,
        update_rate_ms: u32,
    ) -> Result<GroupHandle, CoreError> {
        let name = name.into();
        if self.groups.values().any(|group| group.name == name) {
            return Err(CoreError::GroupNameInUse);
        }
        let handle = GroupHandle(self.next_group_handle);
        self.next_group_handle = self
            .next_group_handle
            .checked_add(1)
            .ok_or(CoreError::HandleSpaceExhausted)?;
        self.groups.insert(
            handle,
            Group {
                name,
                active,
                update_rate_ms: update_rate_ms.max(1),
                next_item_handle: 1,
                items: std::collections::BTreeMap::new(),
                subscribers: Vec::new(),
            },
        );
        Ok(handle)
    }

    pub fn remove_group(&mut self, handle: GroupHandle) -> Result<(), CoreError> {
        self.groups
            .remove(&handle)
            .map(|_| ())
            .ok_or(CoreError::GroupNotFound)
    }

    pub fn group_update_rate(&self, handle: GroupHandle) -> Result<u32, CoreError> {
        self.groups
            .get(&handle)
            .map(|group| group.update_rate_ms)
            .ok_or(CoreError::GroupNotFound)
    }

    pub fn add_item(
        &mut self,
        group_handle: GroupHandle,
        item_id: &str,
        client_handle: u32,
    ) -> Result<ItemHandle, CoreError> {
        let tag = match item_id {
            NUMERIC_ITEM_ID => TagId::NumericDouble,
            BOOLEAN_ITEM_ID => TagId::Boolean,
            _ => return Err(CoreError::UnknownItemId),
        };
        let group = self
            .groups
            .get_mut(&group_handle)
            .ok_or(CoreError::GroupNotFound)?;
        let item_handle = ItemHandle(group.next_item_handle);
        group.next_item_handle = group
            .next_item_handle
            .checked_add(1)
            .ok_or(CoreError::HandleSpaceExhausted)?;
        group
            .items
            .insert(item_handle, GroupItem { tag, client_handle });
        Ok(item_handle)
    }

    pub fn remove_item(
        &mut self,
        group_handle: GroupHandle,
        item_handle: ItemHandle,
    ) -> Result<(), CoreError> {
        let group = self
            .groups
            .get_mut(&group_handle)
            .ok_or(CoreError::GroupNotFound)?;
        group
            .items
            .remove(&item_handle)
            .map(|_| ())
            .ok_or(CoreError::ItemNotFound)
    }

    pub fn read(
        &self,
        group_handle: GroupHandle,
        item_handles: &[ItemHandle],
    ) -> Result<Vec<ItemRead>, CoreError> {
        let group = self
            .groups
            .get(&group_handle)
            .ok_or(CoreError::GroupNotFound)?;
        Ok(item_handles
            .iter()
            .map(|handle| match group.items.get(handle) {
                Some(item) => ItemRead {
                    item_handle: *handle,
                    client_handle: item.client_handle,
                    item_id: item.tag.item_id(),
                    result: Ok(self.model.snapshot(item.tag)),
                },
                None => ItemRead {
                    item_handle: *handle,
                    client_handle: 0,
                    item_id: "",
                    result: Err(CoreError::ItemNotFound),
                },
            })
            .collect())
    }

    /// Writes are rejected regardless of item handle or proposed value.
    pub fn write(
        &self,
        _group_handle: GroupHandle,
        _item_handle: ItemHandle,
        _value: TagValue,
    ) -> Result<(), CoreError> {
        Err(CoreError::WriteRejectedReadOnly)
    }

    /// Registers a local receiver for server-side data-change batches. This is
    /// not a COM connection point; the COM adapter must translate to callbacks.
    pub fn subscribe(
        &mut self,
        group_handle: GroupHandle,
    ) -> Result<std::sync::mpsc::Receiver<Vec<DataChange>>, CoreError> {
        let (sender, receiver) = std::sync::mpsc::channel();
        let group = self
            .groups
            .get_mut(&group_handle)
            .ok_or(CoreError::GroupNotFound)?;
        if group.active && !group.items.is_empty() {
            let initial = group
                .items
                .iter()
                .map(|(item_handle, item)| DataChange {
                    item_handle: *item_handle,
                    client_handle: item.client_handle,
                    item_id: item.tag.item_id(),
                    snapshot: self.model.snapshot(item.tag),
                })
                .collect();
            let _ = sender.send(initial);
        }
        group.subscribers.push(sender);
        Ok(receiver)
    }

    pub fn set_group_active(
        &mut self,
        group_handle: GroupHandle,
        active: bool,
    ) -> Result<(), CoreError> {
        let group = self
            .groups
            .get_mut(&group_handle)
            .ok_or(CoreError::GroupNotFound)?;
        group.active = active;
        Ok(())
    }

    pub fn publish(
        &mut self,
        tag: TagId,
        value: TagValue,
        timestamp: SourceTimestamp,
    ) -> Result<(), CoreError> {
        self.model.publish(tag, value, timestamp)?;
        self.notify(tag);
        Ok(())
    }

    pub fn set_source_available(&mut self, available: bool) {
        self.model.set_source_available(available);
        if !available {
            self.notify(TagId::NumericDouble);
            self.notify(TagId::Boolean);
        }
    }

    fn notify(&mut self, tag: TagId) {
        for group in self.groups.values_mut().filter(|group| group.active) {
            let batch: Vec<_> = group
                .items
                .iter()
                .filter(|(_, item)| item.tag == tag)
                .map(|(item_handle, item)| DataChange {
                    item_handle: *item_handle,
                    client_handle: item.client_handle,
                    item_id: item.tag.item_id(),
                    snapshot: self.model.snapshot(item.tag),
                })
                .collect();
            if batch.is_empty() {
                continue;
            }
            group
                .subscribers
                .retain(|subscriber| subscriber.send(batch.clone()).is_ok());
        }
    }
}
