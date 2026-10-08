#include <winsock2.h>
#include <windows.h>
#include <process.h>

#include <cstdlib>
#include <cstdio>
#include <cstring>
#include <atomic>
#include <string>
#include <vector>

#include "SnapshotStore.h"
#include "ServerApplication.h"
#include "Da/ServerDaAddressSpaceElement.h"
#include "Da/ServerDaTransaction.h"
#include "Da/ServerDaRequest.h"

using namespace SoftingOPCToolboxServer;

namespace {

const char* kTemperature = "OPCBridge.Mirror.Lab.Temperature";
const char* kPressure = "OPCBridge.Mirror.Lab.Pressure";
opcbridge::SnapshotStore g_store;
std::atomic<bool> g_stop(false);

std::string jsonString(const std::string& object, const char* key) {
    const std::string needle = std::string("\"") + key + "\"";
    std::string::size_type p = object.find(needle);
    if (p == std::string::npos) return std::string();
    p = object.find(':', p + needle.size());
    if (p == std::string::npos) return std::string();
    p = object.find('"', p + 1);
    if (p == std::string::npos) return std::string();
    ++p;
    std::string result;
    for (; p < object.size(); ++p) {
        const char c = object[p];
        if (c == '"') return result;
        if (c == '\\' && p + 1 < object.size()) {
            const char escaped = object[++p];
            if (escaped == 'n') result.push_back('\n');
            else if (escaped == 'r') result.push_back('\r');
            else if (escaped == 't') result.push_back('\t');
            else result.push_back(escaped);
        } else result.push_back(c);
    }
    return std::string();
}

std::string quotedValue(const std::string& text, std::string::size_type p) {
    std::string result;
    if (p >= text.size() || text[p] != '"') return result;
    for (++p; p < text.size(); ++p) {
        const char c = text[p];
        if (c == '"') return result;
        if (c == '\\' && p + 1 < text.size()) {
            const char escaped = text[++p];
            if (escaped == 'n') result.push_back('\n');
            else if (escaped == 'r') result.push_back('\r');
            else if (escaped == 't') result.push_back('\t');
            else result.push_back(escaped);
        } else result.push_back(c);
    }
    return std::string();
}

std::string jsonScalar(const std::string& object, const char* key) {
    const std::string needle = std::string("\"") + key + "\"";
    std::string::size_type p = object.find(needle);
    if (p == std::string::npos) return std::string();
    p = object.find(':', p + needle.size());
    if (p == std::string::npos) return std::string();
    ++p;
    while (p < object.size() && (object[p] == ' ' || object[p] == '\t')) ++p;
    if (p < object.size() && object[p] == '"') return quotedValue(object, p);
    std::string::size_type end = object.find_first_of(",} \r\n", p);
    if (end == std::string::npos) end = object.size();
    return object.substr(p, end - p);
}

bool parseRecord(const std::string& object, opcbridge::Snapshot& snapshot) {
    snapshot.itemId = jsonString(object, "item_id");
    if (snapshot.itemId != kTemperature && snapshot.itemId != kPressure) return false;
    snapshot.valueType = jsonString(object, "value_type");
    snapshot.valueText = jsonScalar(object, "value");
    snapshot.timestampUtc = jsonString(object, "timestamp_utc");
    snapshot.qualityCode = static_cast<std::uint16_t>(std::strtoul(jsonScalar(object, "quality_code").c_str(), NULL, 10));
    snapshot.sequence = static_cast<std::uint64_t>(_strtoui64(jsonScalar(object, "sequence").c_str(), NULL, 10));
    snapshot.sourceConnected = jsonScalar(object, "source_connected") == "true";
    snapshot.stale = jsonScalar(object, "is_stale") == "true";
    return !snapshot.valueType.empty();
}

std::vector<opcbridge::Snapshot> parseResponse(const std::string& response) {
    std::vector<opcbridge::Snapshot> records;
    bool quoted = false, escaped = false;
    int depth = 0;
    std::string::size_type start = std::string::npos;
    for (std::string::size_type i = 0; i < response.size(); ++i) {
        const char c = response[i];
        if (quoted) {
            if (escaped) escaped = false;
            else if (c == '\\') escaped = true;
            else if (c == '"') quoted = false;
            continue;
        }
        if (c == '"') quoted = true;
        else if (c == '{') { if (depth++ == 0) start = i; }
        else if (c == '}' && depth > 0 && --depth == 0 && start != std::string::npos) {
            opcbridge::Snapshot snapshot;
            if (parseRecord(response.substr(start, i - start + 1), snapshot)) records.push_back(snapshot);
            start = std::string::npos;
        }
    }
    return records;
}

unsigned __stdcall pollBridge(void*) {
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) { g_store.markDisconnected(); return 1; }
    while (!g_stop.load()) {
        SOCKET socketHandle = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        bool connected = false;
        if (socketHandle != INVALID_SOCKET) {
            sockaddr_in address;
            std::memset(&address, 0, sizeof(address));
            address.sin_family = AF_INET;
            address.sin_port = htons(45871);
            address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
            if (connect(socketHandle, reinterpret_cast<sockaddr*>(&address), sizeof(address)) == 0) {
                const char request[] = "{\"op\":\"snapshot\",\"items\":[\"OPCBridge.Mirror.Lab.Temperature\",\"OPCBridge.Mirror.Lab.Pressure\"]}\n";
                    int sent = 0;
                    const int requestLength = static_cast<int>(sizeof(request) - 1);
                    while (sent < requestLength) {
                        const int count = send(socketHandle, request + sent, requestLength - sent, 0);
                        if (count <= 0) break;
                        sent += count;
                    }
                    if (sent == requestLength) {
                    std::string response;
                    char buffer[4096];
                    while (response.size() <= 1048576) {
                        const int count = recv(socketHandle, buffer, sizeof(buffer), 0);
                        if (count <= 0) break;
                        response.append(buffer, count);
                        if (response.find('\n') != std::string::npos) { connected = true; break; }
                    }
                    if (connected) {
                        const std::vector<opcbridge::Snapshot> records = parseResponse(response);
                        bool gotTemperature = false, gotPressure = false;
                        for (std::vector<opcbridge::Snapshot>::const_iterator it = records.begin(); it != records.end(); ++it) {
                            g_store.apply(*it);
                            if (it->itemId == kTemperature) gotTemperature = true;
                            if (it->itemId == kPressure) gotPressure = true;
                        }
                        connected = gotTemperature && gotPressure;
                    }
                }
            }
            closesocket(socketHandle);
        }
        if (!connected) g_store.markDisconnected();
        Sleep(500);
    }
    WSACleanup();
    return 0;
}

bool toDateTime(const std::string& utc, DateTime& result) {
    SYSTEMTIME st;
    std::memset(&st, 0, sizeof(st));
    int millis = 0;
    if (std::sscanf(utc.c_str(), "%hu-%hu-%huT%hu:%hu:%hu.%dZ", &st.wYear, &st.wMonth, &st.wDay,
                    &st.wHour, &st.wMinute, &st.wSecond, &millis) < 6) return false;
    st.wMilliseconds = static_cast<WORD>(millis);
    FILETIME ft;
    if (!SystemTimeToFileTime(&st, &ft)) return false;
    result.set(&ft);
    return true;
}

class MirrorTag : public DaAddressSpaceElement {
public:
    MirrorTag(const tstring& name, const tstring& itemId, VARENUM dataType) : DaAddressSpaceElement() {
        tstring mutableName(name), mutableId(itemId);
        setName(mutableName);
        setItemId(mutableId);
        setAccessRights(EnumAccessRights_READABLE);
        setDatatype(dataType);
        setIoMode(EnumIoMode_POLL);
        setHasChildren(FALSE);
    }
};

class MirrorRoot : public DaAddressSpaceRoot {
public:
    long queryAddressSpaceElementChildren(const tstring& elementId, std::vector<AddressSpaceElement*>& children) {
        // Future dynamic browse/callback hook. Initial namespace is materialized in buildNamespace().
        (void)elementId; (void)children;
        return EnumResultCode_S_OK;
    }
};

class MirrorTransaction : public DaTransaction {
public:
    MirrorTransaction(EnumTransactionType type, std::vector<DaRequest*>& requests, unsigned long session)
        : DaTransaction(type, requests, session) {}

    long handleReadRequests() {
        for (std::vector<DaRequest*>::iterator it = m_requestList.begin(); it != m_requestList.end(); ++it) {
            DaRequest* request = *it;
            if (!request || !request->progressRequestState(EnumRequestState_CREATED, EnumRequestState_INPROGRESS)) continue;
            MirrorTag* tag = dynamic_cast<MirrorTag*>(request->getAddressSpaceElement());
            opcbridge::Snapshot snapshot;
            if (!tag || request->getPropertyId() != 0 || !g_store.copy(narrowId(tag->getItemId()), snapshot)) {
                request->setResult(EnumResultCode_E_OPC_UNKNOWNITEMID);
                continue;
            }
            Variant value;
            if (!makeVariant(snapshot, value)) {
                request->setResult(EnumResultCode_E_OPC_BADTYPE);
                continue;
            }
            DateTime timestamp;
            if (!toDateTime(snapshot.timestampUtc, timestamp)) timestamp.now();
            ValueQT vqt(value, static_cast<EnumQuality>(opcbridge::effectiveQuality(snapshot)), timestamp);
            request->setValue(vqt);
            request->setResult(EnumResultCode_S_OK);
        }
        return completeRequests();
    }

    long handleWriteRequests() {
        for (std::vector<DaRequest*>::iterator it = m_requestList.begin(); it != m_requestList.end(); ++it) {
            DaRequest* request = *it;
            if (request && request->progressRequestState(EnumRequestState_CREATED, EnumRequestState_INPROGRESS))
                request->setResult(EnumResultCode_E_ACCESSDENIED);
        }
        return completeRequests();
    }

private:
    static std::string narrowId(const tstring& value) {
#ifdef UNICODE
        const int bytes = WideCharToMultiByte(CP_UTF8, 0, value.c_str(), -1, NULL, 0, NULL, NULL);
        std::string result(bytes > 0 ? bytes : 0, '\0');
        if (bytes > 0) {
            WideCharToMultiByte(CP_UTF8, 0, value.c_str(), -1, &result[0], bytes, NULL, NULL);
            result.resize(bytes - 1);
        }
        return result;
#else
        return value;
#endif
    }

    static bool makeVariant(const opcbridge::Snapshot& snapshot, Variant& value) {
        if (snapshot.valueType == "float") { value = std::strtod(snapshot.valueText.c_str(), NULL); return true; }
        if (snapshot.valueType == "i32") { value = static_cast<long>(std::strtol(snapshot.valueText.c_str(), NULL, 10)); return true; }
        if (snapshot.valueType == "i16") { value = static_cast<short>(std::strtol(snapshot.valueText.c_str(), NULL, 10)); return true; }
        if (snapshot.valueType == "bool") { value = snapshot.valueText == "true"; return true; }
        if (snapshot.valueType == "string") {
#ifdef UNICODE
            const int chars = MultiByteToWideChar(CP_UTF8, 0, snapshot.valueText.c_str(), -1, NULL, 0);
            std::vector<wchar_t> buffer(chars ? chars : 1);
            if (chars) MultiByteToWideChar(CP_UTF8, 0, snapshot.valueText.c_str(), -1, &buffer[0], chars);
            value = Variant(&buffer[0]);
#else
            value = Variant(snapshot.valueText.c_str());
#endif
            return true;
        }
        return false; // Byte blobs need an explicit OPC array contract before exposure.
    }
};

class MirrorCreator : public Creator {
public:
    DaAddressSpaceRoot* createDaAddressSpaceRoot() { return new MirrorRoot(); }
    DaTransaction* createTransaction(EnumTransactionType type, std::vector<DaRequest*>& requests, unsigned long session) {
        return new MirrorTransaction(type, requests, session);
    }
};

long buildNamespace() {
    AddressSpaceElement* root = getApp()->getDaAddressSpaceRoot();
    AddressSpaceElement* bridge = new MirrorTag(_T("OPCBridge"), _T("OPCBridge"), VT_EMPTY);
    bridge->setIoMode(EnumIoMode_NONE); bridge->setHasChildren(TRUE);
    root->addChild(bridge);
    AddressSpaceElement* mirror = new MirrorTag(_T("Mirror"), _T("OPCBridge.Mirror"), VT_EMPTY);
    mirror->setIoMode(EnumIoMode_NONE); mirror->setHasChildren(TRUE);
    bridge->addChild(mirror);
    AddressSpaceElement* lab = new MirrorTag(_T("Lab"), _T("OPCBridge.Mirror.Lab"), VT_EMPTY);
    lab->setIoMode(EnumIoMode_NONE); lab->setHasChildren(TRUE);
    mirror->addChild(lab);
    lab->addChild(new MirrorTag(_T("Temperature"), _T("OPCBridge.Mirror.Lab.Temperature"), VT_R8));
    lab->addChild(new MirrorTag(_T("Pressure"), _T("OPCBridge.Mirror.Lab.Pressure"), VT_R8));
    return EnumResultCode_S_OK;
}

} // namespace

int wmain() {
    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) return 2;
    MirrorCreator creator;
    opcbridge::Snapshot initial = {kTemperature, "float", "0", "", 0x0008, 0, false, true};
    g_store.apply(initial);
    initial.itemId = kPressure;
    g_store.apply(initial);
    Application* app = getApp();
    app->setVersionOtb(447);
    app->setAppType(EnumApplicationType_EXECUTABLE);
    app->setClsidDa(_T("{B7A45C89-4D3B-4921-9E9A-8F5D890E71E2}"));
    app->setProgIdDa(_T("OPCBridge.Mirror.1"));
    app->setVerIndProgIdDa(_T("OPCBridge.Mirror"));
    app->setDescription(_T("OPCBridge Mirror"));
    app->setAddressSpaceDelimiter(_T('.'));
    app->setMinUpdateRateDa(250);
    if (FAILED(app->initialize(&creator)) || FAILED(app->start()) || FAILED(buildNamespace()) || FAILED(app->ready())) {
        app->terminate(); releaseApp(); WSACleanup(); return 3;
    }
    const uintptr_t worker = _beginthreadex(NULL, 0, pollBridge, NULL, 0, NULL);
    if (!worker) { g_stop = true; app->stop(); app->terminate(); releaseApp(); WSACleanup(); return 4; }
    while (!GetAsyncKeyState(VK_ESCAPE) && !g_stop.load()) Sleep(250);
    g_stop.store(true);
    WaitForSingleObject(reinterpret_cast<HANDLE>(worker), INFINITE);
    CloseHandle(reinterpret_cast<HANDLE>(worker));
    app->stop(); app->terminate(); releaseApp(); WSACleanup();
    return 0;
}
