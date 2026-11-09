using System;

namespace OPCBridge.Mirror.CSharp
{
    /// <summary>Portable snapshot rules shared by the simulated source and Softing adapter.</summary>
    public sealed class LabSnapshot
    {
        public const ushort Good = 0x00C0;
        public const ushort Bad = 0x0000;
        public const ushort BadNotConnected = 0x0008;
        public const ushort BadCommunicationFailure = 0x0018;
        public const ushort BadWaitingForInitialData = 0x0020;
        public const int WriteDeniedHresult = unchecked((int)0xC0040006);

        public string ItemId { get; private set; }
        public Type ValueType { get; private set; }
        public object Value { get; private set; }
        public bool HasValue { get; private set; }
        public ushort Quality { get; private set; }
        public DateTime? SourceTimestampUtc { get; private set; }
        public bool SourceConnected { get; private set; }

        private LabSnapshot(string itemId, Type valueType, object value, bool hasValue,
            ushort quality, DateTime? sourceTimestampUtc, bool sourceConnected)
        {
            ItemId = itemId;
            ValueType = valueType;
            Value = value;
            HasValue = hasValue;
            Quality = quality;
            SourceTimestampUtc = sourceTimestampUtc;
            SourceConnected = sourceConnected;
        }

        public static LabSnapshot Initial(string itemId, Type valueType)
        {
            if (String.IsNullOrWhiteSpace(itemId)) throw new ArgumentException("Item ID is required", "itemId");
            if (valueType != typeof(double) && valueType != typeof(bool))
                throw new ArgumentException("The laboratory server supports only Double and Boolean tags", "valueType");
            object initialValue = valueType == typeof(double) ? (object)0.0 : false;
            return new LabSnapshot(itemId, valueType, initialValue, false,
                BadWaitingForInitialData, null, false);
        }

        public static LabSnapshot FromSource(LabSnapshot previous, object value, DateTime? timestampUtc)
        {
            if (previous == null) throw new ArgumentNullException("previous");
            if (value == null || value.GetType() != previous.ValueType)
                throw new ArgumentException("Returned value does not match the declared DA item type", "value");
            if (value is double && (Double.IsNaN((double)value) || Double.IsInfinity((double)value)))
                throw new ArgumentException("Numeric sample must be finite", "value");

            bool timestampValid = IsValidTimestamp(timestampUtc);
            return new LabSnapshot(previous.ItemId, previous.ValueType, value, true,
                timestampValid ? Good : Bad, timestampValid ? timestampUtc : null, true);
        }

        public static LabSnapshot Disconnected(LabSnapshot previous)
        {
            if (previous == null) throw new ArgumentNullException("previous");
            ushort quality = previous.HasValue ? BadCommunicationFailure : BadNotConnected;
            return new LabSnapshot(previous.ItemId, previous.ValueType, previous.Value,
                previous.HasValue, quality, previous.SourceTimestampUtc, false);
        }

        public static bool IsValidTimestamp(DateTime? timestampUtc)
        {
            if (!timestampUtc.HasValue) return false;
            DateTime value = timestampUtc.Value;
            if (value.Kind != DateTimeKind.Utc) return false;
            if (value.Ticks <= FileTimeEpoch.Ticks) return false;
            if (value > DateTime.UtcNow.AddSeconds(5)) return false;
            return true;
        }

        /// <summary>
        /// Softing ValueQT requires a DateTime. FILETIME epoch is the explicit no-timestamp sentinel;
        /// callers must keep the corresponding quality Bad and must never substitute the local clock.
        /// </summary>
        public static readonly DateTime FileTimeEpoch =
            new DateTime(1601, 1, 1, 0, 0, 0, DateTimeKind.Utc);

        public DateTime ToSdkTimestamp()
        {
            return IsValidTimestamp(SourceTimestampUtc) ? SourceTimestampUtc.Value : FileTimeEpoch;
        }
    }
}
