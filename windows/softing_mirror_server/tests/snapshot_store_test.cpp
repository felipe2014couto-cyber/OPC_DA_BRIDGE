#include "../src/SnapshotStore.h"

#include <cassert>

int main() {
    opcbridge::SnapshotStore store;
    opcbridge::Snapshot s = {"OPCBridge.Mirror.Lab.Temperature", "float", "23.5", "2026-01-01T00:00:00Z", 0x00C0, 5, true, false};
    assert(store.apply(s));
    assert(!store.apply(opcbridge::Snapshot{"OPCBridge.Mirror.Lab.Temperature", "float", "10", "2026-01-01T00:00:00Z", 0x00C0, 4, true, false}));

    opcbridge::Snapshot current;
    assert(store.copy(s.itemId, current));
    assert(current.valueText == "23.5");
    assert(opcbridge::effectiveQuality(current) == 0x00C0);

    current.stale = true;
    assert(opcbridge::effectiveQuality(current) == 0x0018);
    current.sourceConnected = false;
    assert(opcbridge::effectiveQuality(current) == 0x0008);

    store.markDisconnected();
    assert(store.copy(s.itemId, current));
    assert(!current.sourceConnected && current.stale);
    assert(opcbridge::effectiveQuality(current) == 0x0008);
    assert(!store.copy("missing", current));
    return 0;
}
