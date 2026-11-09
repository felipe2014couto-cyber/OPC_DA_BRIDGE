using System;
using OPCBridge.Mirror.CSharp;

internal static class ModelTests
{
    private static int _count;

    private static void Check(bool condition, string message)
    {
        _count++;
        if (!condition) throw new Exception("FAIL: " + message);
    }

    private static void Throws<T>(Action action, string message) where T : Exception
    {
        try { action(); }
        catch (T) { _count++; return; }
        throw new Exception("FAIL: expected " + typeof(T).Name + ": " + message);
    }

    private static int Main()
    {
        LabSnapshot initial = LabSnapshot.Initial("Lab.Numeric", typeof(double));
        Check(!initial.HasValue && initial.Quality != LabSnapshot.Good, "initial is not Good");
        Check(!initial.SourceTimestampUtc.HasValue, "initial timestamp is absent");
        Check(unchecked((uint)LabSnapshot.WriteDeniedHresult) == 0xC0040006,
            "writes map to OPC_E_BADRIGHTS");

        DateTime timestamp = new DateTime(2026, 10, 1, 12, 30, 15, DateTimeKind.Utc);
        LabSnapshot good = LabSnapshot.FromSource(initial, 42.5, timestamp);
        Check(good.Quality == LabSnapshot.Good && good.Value.Equals(42.5), "numeric sample is Good");
        Check(good.SourceTimestampUtc.Value == timestamp, "source timestamp retained");

        LabSnapshot boolean = LabSnapshot.FromSource(LabSnapshot.Initial("Lab.Boolean", typeof(bool)), true, timestamp);
        Check(boolean.ValueType == typeof(bool) && boolean.Value.Equals(true), "Boolean type retained");

        LabSnapshot missingTime = LabSnapshot.FromSource(initial, 5.0, null);
        Check(missingTime.Quality != LabSnapshot.Good && !missingTime.SourceTimestampUtc.HasValue,
            "missing timestamp cannot be Good");
        Check(missingTime.ToSdkTimestamp() == LabSnapshot.FileTimeEpoch, "SDK sentinel is FILETIME epoch");
        Check(LabSnapshot.FromSource(initial, 5.0, DateTime.SpecifyKind(timestamp, DateTimeKind.Local)).Quality != LabSnapshot.Good,
            "local timestamp rejected");
        Check(LabSnapshot.FromSource(initial, 5.0, LabSnapshot.FileTimeEpoch).Quality != LabSnapshot.Good,
            "epoch timestamp rejected as absent");
        Check(LabSnapshot.FromSource(initial, 5.0, DateTime.UtcNow.AddMinutes(1)).Quality != LabSnapshot.Good,
            "future timestamp rejected");
        Throws<ArgumentException>(() => LabSnapshot.FromSource(initial, 5, timestamp), "type mismatch");
        Throws<ArgumentException>(() => LabSnapshot.FromSource(initial, Double.NaN, timestamp), "NaN rejected");

        LabSnapshot disconnected = LabSnapshot.Disconnected(good);
        Check(!disconnected.SourceConnected && disconnected.Quality != LabSnapshot.Good,
            "disconnect degrades quality");
        Check(disconnected.Value.Equals(good.Value) && disconnected.SourceTimestampUtc == good.SourceTimestampUtc,
            "disconnect retains cache only as Bad with original timestamp");
        LabSnapshot recovered = LabSnapshot.FromSource(disconnected, 43.0, timestamp.AddSeconds(1));
        Check(recovered.SourceConnected && recovered.Quality == LabSnapshot.Good, "valid sample recovers");

        Console.WriteLine("PASS: " + _count + " model assertions; runtime=" +
            (Environment.Is64BitProcess ? "x64" : "x86") + "; CLR=" + Environment.Version);
        return 0;
    }
}
