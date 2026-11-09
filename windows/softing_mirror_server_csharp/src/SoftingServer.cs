using System;
using System.Collections;
using System.IO;
using Softing.OPCToolbox;
using Softing.OPCToolbox.Server;

namespace OPCBridge.Mirror.CSharp
{
    internal sealed class LabLog
    {
        private readonly object _sync = new object();
        private readonly StreamWriter _writer;

        public LabLog(string path)
        {
            _writer = new StreamWriter(path, true);
            _writer.AutoFlush = true;
        }

        public void Write(string level, string message)
        {
            lock (_sync)
            {
                string line = DateTime.UtcNow.ToString("o") + " [" + level + "] " + message;
                _writer.WriteLine(line);
                Console.WriteLine(line);
            }
        }

        public void Dispose() { lock (_sync) { _writer.Dispose(); } }
    }

    internal sealed class LabCreator : Creator
    {
        public override DaAddressSpaceRoot CreateDaAddressSpaceRoot() { return new LabAddressSpaceRoot(); }
        public override DaTransaction CreateTransaction(EnumTransactionType type, DaRequest[] requests, uint sessionKey)
        { return new LabTransaction(type, requests, sessionKey); }
        public override DaAddressSpaceElement CreateInternalDaAddressSpaceElement(string itemId, string name,
            uint userData, uint objectHandle, uint parentHandle)
        { return new DaAddressSpaceElement(itemId, name, userData, objectHandle, parentHandle); }
    }

    internal sealed class LabAddressSpaceRoot : DaAddressSpaceRoot
    {
        public override int QueryAddressSpaceElementData(string elementId, out AddressSpaceElement element)
        { element = null; return (int)EnumResultCode.E_BADTYPE; }
        public override int QueryAddressSpaceElementChildren(string elementId, ArrayList children)
        { return (int)EnumResultCode.S_OK; }
        public override int QueryAddressSpaceElementProperties(string elementId, out ArrayList properties)
        { properties = new ArrayList(); return (int)EnumResultCode.S_OK; }
    }

    internal sealed class LabTransaction : DaTransaction
    {
        public LabTransaction(EnumTransactionType type, DaRequest[] requests, uint sessionKey)
            : base(type, requests, sessionKey) { }

        public override int HandleReadRequests()
        {
            foreach (object entry in Requests)
            {
                DaRequest request = entry as DaRequest;
                if (request == null || !request.ProgressRequestState(EnumRequestState.CREATED, EnumRequestState.INPROGRESS))
                    continue;
                if (request.PropertyId == 0)
                {
                    ValueQT value = null;
                    request.AddressSpaceElement.GetCacheValue(ref value);
                    request.Value = value;
                    request.Result = EnumResultCode.S_OK;
                }
                else request.Result = EnumResultCode.E_BADTYPE;
            }
            return CompleteRequests();
        }

        public override int HandleWriteRequests()
        {
            foreach (object entry in Requests)
            {
                DaRequest request = entry as DaRequest;
                if (request != null && request.ProgressRequestState(EnumRequestState.CREATED, EnumRequestState.INPROGRESS))
                    request.Result = (EnumResultCode)LabSnapshot.WriteDeniedHresult;
            }
            return CompleteRequests();
        }
    }

    internal sealed class LabTag : DaAddressSpaceElement
    {
        public readonly string Key;
        public LabTag(string key, string name, Type type) : base()
        {
            Key = key;
            Name = name;
            ItemId = key;
            Datatype = type;
            AccessRights = EnumAccessRights.READABLE;
            IoMode = EnumIoMode.REPORT;
            HasChildren = false;
        }

        public void Publish(LabSnapshot snapshot)
        {
            ValueChanged(new ValueQT(snapshot.Value, (EnumQuality)snapshot.Quality, snapshot.ToSdkTimestamp()));
        }
    }
}
