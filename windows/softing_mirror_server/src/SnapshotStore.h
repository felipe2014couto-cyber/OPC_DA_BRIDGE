#pragma once

#include <cstdint>
#include <map>
#include <mutex>
#include <string>

namespace opcbridge {

struct Snapshot {
    std::string itemId;
    std::string valueType;
    std::string valueText;
    std::string timestampUtc;
    std::uint16_t qualityCode;
    std::uint64_t sequence;
    bool sourceConnected;
    bool stale;
};

// Thread-safe, platform-neutral latest-value cache shared by bridge and DA callbacks.
class SnapshotStore {
public:
    bool apply(const Snapshot& snapshot);
    void markDisconnected();
    bool copy(const std::string& itemId, Snapshot& snapshot) const;

private:
    mutable std::mutex mutex_;
    std::map<std::string, Snapshot> snapshots_;
};

std::uint16_t effectiveQuality(const Snapshot& snapshot);

} // namespace opcbridge
