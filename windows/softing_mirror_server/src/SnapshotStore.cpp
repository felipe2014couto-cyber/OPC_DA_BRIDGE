#include "SnapshotStore.h"

namespace opcbridge {

bool SnapshotStore::apply(const Snapshot& incoming) {
    std::lock_guard<std::mutex> lock(mutex_);
    const std::map<std::string, Snapshot>::const_iterator old = snapshots_.find(incoming.itemId);
    if (old != snapshots_.end() && incoming.sequence < old->second.sequence) {
        return false;
    }
    snapshots_[incoming.itemId] = incoming;
    return true;
}

void SnapshotStore::markDisconnected() {
    std::lock_guard<std::mutex> lock(mutex_);
    for (std::map<std::string, Snapshot>::iterator it = snapshots_.begin(); it != snapshots_.end(); ++it) {
        it->second.sourceConnected = false;
        it->second.stale = true;
        it->second.qualityCode = 0x0008; // OPC_QUALITY_BAD_NOT_CONNECTED
    }
}

bool SnapshotStore::copy(const std::string& itemId, Snapshot& snapshot) const {
    std::lock_guard<std::mutex> lock(mutex_);
    const std::map<std::string, Snapshot>::const_iterator it = snapshots_.find(itemId);
    if (it == snapshots_.end()) return false;
    snapshot = it->second;
    return true;
}

std::uint16_t effectiveQuality(const Snapshot& snapshot) {
    const std::uint16_t quality = snapshot.qualityCode;
    if (!snapshot.sourceConnected) return 0x0008; // BAD_NOT_CONNECTED
    if (snapshot.stale && (quality & 0x00C0) == 0x00C0) return 0x0018; // BAD_COMM_FAILURE
    return quality;
}

} // namespace opcbridge
