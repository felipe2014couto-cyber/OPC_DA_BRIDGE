using System;
using System.Threading;
using Softing.OPCToolbox;
using Softing.OPCToolbox.Server;

namespace OPCBridge.Mirror.CSharp
{
    internal static class Program
    {
        private const string ProgId = "OPCBridge.Mirror.CSharp.1";
        private const string VersionIndependentProgId = "OPCBridge.Mirror.CSharp";
        private const string Clsid = "{7E48D58B-AD04-4A24-9D02-C07F21E5B5C9}";
        private static LabLog _log;
        private static LabTag _numericTag;
        private static LabTag _booleanTag;
        private static LabSnapshot _numeric;
        private static LabSnapshot _boolean;

        private static int Main(string[] args)
        {
            bool registrationCommand = args.Length == 1 &&
                (String.Equals(args[0], "/RegServer", StringComparison.OrdinalIgnoreCase) ||
                 String.Equals(args[0], "/UnregServer", StringComparison.OrdinalIgnoreCase));
            if (args.Length != 0 && !registrationCommand)
            {
                Console.Error.WriteLine("Usage: OPCBridge.Mirror.CSharp.exe [/RegServer|/UnregServer]");
                return 2;
            }

            try
            {
                if (!registrationCommand) _log = new LabLog("OPCBridge.Mirror.CSharp.log");
                Application app = Application.Instance;
                app.VersionOtb = 447;
                app.AppType = EnumApplicationType.EXECUTABLE;
                app.ClsidDa = Clsid;
                app.ProgIdDa = ProgId;
                app.VerIndProgIdDa = VersionIndependentProgId;
                app.Description = "OPC DA simulated read-only mirror laboratory";
                app.MajorVersion = 1;
                app.MinorVersion = 0;
                app.PatchVersion = 0;
                app.BuildNumber = 0;
                app.VendorInfo = "OPC_DA_BRIDGE laboratory prototype";
                app.MinUpdateRateDa = 100;
                app.ServiceName = String.Empty;
                app.ClientCheckPeriod = 30000;
                app.AddressSpaceDelimiter = '.';
                app.PropertyDelimiter = '#';
                app.ShutdownRequest += new ShutdownHandler(Shutdown);

                LabCreator creator = new LabCreator();
                int result = app.Initialize(creator);
                if (ResultCode.FAILED(result)) throw new InvalidOperationException("Softing Initialize failed: 0x" + result.ToString("X8"));

                if (registrationCommand)
                {
                    // Registration is intentionally opt-in and never used by the normal startup path.
                    return app.ProcessCommandLine(Environment.CommandLine);
                }

                _numeric = LabSnapshot.Initial("OPCBridge.Mirror.CSharp.Lab.Numeric", typeof(double));
                _boolean = LabSnapshot.Initial("OPCBridge.Mirror.CSharp.Lab.Running", typeof(bool));
                _numericTag = new LabTag(_numeric.ItemId, "Numeric", typeof(double));
                _booleanTag = new LabTag(_boolean.ItemId, "Running", typeof(bool));

                result = app.Start();
                if (ResultCode.FAILED(result)) throw new InvalidOperationException("Softing Start failed: 0x" + result.ToString("X8"));
                DaAddressSpaceElement parent = app.DaAddressSpaceRoot;
                parent = AddFolder(parent, "OPCBridge", "OPCBridge");
                parent = AddFolder(parent, "Mirror", "OPCBridge.Mirror");
                parent = AddFolder(parent, "CSharp", "OPCBridge.Mirror.CSharp");
                parent = AddFolder(parent, "Lab", "OPCBridge.Mirror.CSharp.Lab");
                parent.AddChild(_numericTag);
                parent.AddChild(_booleanTag);
                _numericTag.Publish(_numeric);
                _booleanTag.Publish(_boolean);
                result = app.Ready();
                if (ResultCode.FAILED(result)) throw new InvalidOperationException("Softing Ready failed: 0x" + result.ToString("X8"));

                _log.Write("INFO", "server started; ProgID=" + ProgId + "; source=simulated; tags=2; writes=denied");
                _log.Write("INFO", "source state=connected; waiting for first sample; initial quality=Bad_WaitingForInitialData");
                RunSimulator();
                _log.Write("INFO", "server stopping");
                app.Stop();
                app.Terminate();
                _log.Dispose();
                return 0;
            }
            catch (Exception ex)
            {
                if (_log != null) _log.Write("ERROR", ex.ToString());
                else Console.Error.WriteLine(ex.ToString());
                try { Application.Instance.Stop(); Application.Instance.Terminate(); } catch { }
                if (_log != null) _log.Dispose();
                return 1;
            }
        }

        private static int Shutdown() { return (int)EnumResultCode.S_OK; }

        private static DaAddressSpaceElement AddFolder(DaAddressSpaceElement parent, string name, string itemId)
        {
            DaAddressSpaceElement folder = new DaAddressSpaceElement();
            folder.Name = name;
            folder.ItemId = itemId;
            folder.HasChildren = true;
            folder.IoMode = EnumIoMode.NONE;
            parent.AddChild(folder);
            return folder;
        }

        private static void RunSimulator()
        {
            bool sourceConnected = true;
            int cycle = 0;
            double baseline = 21.0;
            _log.Write("INFO", "press P to toggle simulated source availability; Q to stop");
            while (true)
            {
                if (Console.KeyAvailable)
                {
                    ConsoleKey key = Console.ReadKey(true).Key;
                    if (key == ConsoleKey.Q) break;
                    if (key == ConsoleKey.P)
                    {
                        sourceConnected = !sourceConnected;
                        _log.Write("INFO", "simulated source state=" + (sourceConnected ? "connected" : "disconnected"));
                        if (!sourceConnected)
                        {
                            _numeric = LabSnapshot.Disconnected(_numeric);
                            _boolean = LabSnapshot.Disconnected(_boolean);
                            _numericTag.Publish(_numeric);
                            _booleanTag.Publish(_boolean);
                        }
                    }
                }

                if (sourceConnected)
                {
                    cycle++;
                    DateTime sourceTime = DateTime.UtcNow;
                    _numeric = LabSnapshot.FromSource(_numeric, baseline + Math.Sin(cycle / 10.0), sourceTime);
                    _boolean = LabSnapshot.FromSource(_boolean, (cycle / 10) % 2 == 0, sourceTime);
                    _numericTag.Publish(_numeric);
                    _booleanTag.Publish(_boolean);
                }
                Thread.Sleep(1000);
            }
        }
    }
}
