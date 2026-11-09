//! Shared, OPC-independent configuration and simulated cache. No COM bindings.
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::io::{Read, Write};
use std::path::Path;
use std::time::Duration;

pub const DEFAULT_PORT: u16 = 49321;
pub const MAX_CONFIG_BYTES: u64 = 1_048_576;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum DataType {
    Boolean,
    Double,
    Integer,
    String,
}

impl DataType {
    pub fn vartype(self) -> u16 {
        match self {
            Self::Boolean => 11,
            Self::Double => 5,
            Self::Integer => 3,
            Self::String => 8,
        }
    }
}

pub fn is_supported_scalar_vartype(vartype: u16) -> bool {
    matches!(vartype, 2 | 3 | 4 | 5 | 8 | 11 | 16 | 17 | 18 | 19 | 20 | 21 | 22 | 23)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum SourceDataType {
    Auto,
    Integer,
    String,
    Boolean,
    Double,
    Float,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum SourceConversion {
    #[serde(rename = "automatic")]
    Automatic,
    #[serde(rename = "float_to_double")]
    FloatToDouble,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum SimValue {
    Boolean(bool),
    Integer(i32),
    Double(f64),
    String(String),
}
impl SimValue {
    pub fn data_type(&self) -> DataType {
        match self {
            Self::Boolean(_) => DataType::Boolean,
            Self::Double(_) => DataType::Double,
            Self::Integer(_) => DataType::Integer,
            Self::String(_) => DataType::String,
        }
    }
    /// JSON does not distinguish integral Doubles from Integers. The declared
    /// output type is authoritative; normalize only lossless numeric forms.
    pub fn for_type(self, data_type: DataType) -> Option<Self> {
        match (data_type, self) {
            (DataType::Double, Self::Integer(v)) => Some(Self::Double(f64::from(v))),
            (DataType::Double, Self::Double(v)) if v.is_finite() => Some(Self::Double(v)),
            (DataType::Integer, Self::Integer(v)) => Some(Self::Integer(v)),
            (DataType::Integer, Self::Double(v)) if v.is_finite() && v.fract() == 0.0
                && v >= f64::from(i32::MIN) && v <= f64::from(i32::MAX) => Some(Self::Integer(v as i32)),
            (DataType::Boolean, Self::Boolean(v)) => Some(Self::Boolean(v)),
            (DataType::String, Self::String(v)) if v.encode_utf16().count() <= 4096 => Some(Self::String(v)),
            _ => None,
        }
    }
    pub fn display(&self) -> String {
        match self {
            Self::Boolean(v) => v.to_string(),
            Self::Double(v) => v.to_string(),
            Self::Integer(v) => v.to_string(),
            Self::String(v) => v.clone(),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SourceBinding {
    pub module_id: String,
    pub item_id: String,
    pub stale_after_s: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_data_type: Option<SourceDataType>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_conversion: Option<SourceConversion>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Variable {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source: Option<SourceBinding>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub item_id: Option<String>,
    pub name: String,
    pub data_type: DataType,
    #[serde(default)]
    pub description: String,
    /// Reserved configuration only. No UA client is implemented.
    #[serde(default)]
    pub node_id: String,
    pub initial_value: SimValue,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Module {
    pub name: String,
    #[serde(default)]
    pub variables: Vec<Variable>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Connection {
    pub name: String,
    #[serde(default)]
    pub modules: Vec<Module>,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Deployment {
    pub machine_id: String,
    pub configuration_revision: u64,
}
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Project {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub deployment: Option<Deployment>,
    pub schema_version: u32,
    pub name: String,
    pub diagnostic_port: u16,
    #[serde(default)]
    pub connections: Vec<Connection>,
}
impl Default for Project {
    fn default() -> Self {
        Self {
            deployment: None,
            schema_version: 1,
            name: "Laboratório".into(),
            diagnostic_port: DEFAULT_PORT,
            connections: vec![Connection {
                name: "Simulador local".into(),
                modules: Vec::new(),
            }],
        }
    }
}
#[derive(Clone, Debug)]
pub struct PublishedVariable {
    pub item_id: String,
    pub variable: Variable,
}

pub fn item_id(module: &str, variable: &str) -> String {
    format!("{module}.{variable}")
}
fn identifier(value: &str) -> bool {
    let mut chars = value.chars();
    matches!(chars.next(), Some(c) if c.is_ascii_alphabetic() || c=='_')
        && value.len() <= 80
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}
fn label(value: &str) -> bool {
    !value.trim().is_empty() && value.len() <= 240 && !value.chars().any(char::is_control)
}
impl Project {
    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != 1 {
            return Err("Versão de configuração não suportada.".into());
        }
        if !label(&self.name) {
            return Err("Informe um nome de projeto válido.".into());
        }
        if self.diagnostic_port == 0 {
            return Err("A porta local deve estar entre 1 e 65535.".into());
        }
        let explicit = self.connections.iter().flat_map(|c| &c.modules).flat_map(|m| &m.variables).all(|v| v.item_id.is_some());
        let mut ids = BTreeSet::new();
        let mut connections = BTreeSet::new();
        let mut modules = BTreeSet::new();
        let mut count = 0;
        for connection in &self.connections {
            if !label(&connection.name) || !connections.insert(connection.name.to_lowercase()) {
                return Err("Nome de conexão vazio, inválido ou duplicado.".into());
            }
            for module in &connection.modules {
                if !(if explicit { label(&module.name) } else { identifier(&module.name) }) {
                    return Err(format!("Módulo '{}': use letras ASCII, números e _, começando por letra ou _ (máximo 80).",module.name));
                }
                if !modules.insert(module.name.to_lowercase()) {
                    return Err(format!("O módulo '{}' já existe no projeto.", module.name));
                }
                let mut variables = BTreeSet::new();
                for variable in &module.variables {
                    count += 1;
                    let id = variable.item_id.clone().unwrap_or_else(|| item_id(&module.name, &variable.name));
                    if id.is_empty() || id.len() > 512 || id.chars().any(|c| c.is_control() || c.is_whitespace()) || !ids.insert(id) {
                        return Err("ItemID inválido ou duplicado.".into());
                    }
                    if !(if explicit { label(&variable.name) } else { identifier(&variable.name) }) {
                        return Err(format!("Variável '{}': use letras ASCII, números e _, começando por letra ou _ (máximo 80).",variable.name));
                    }
                    if !variables.insert(variable.name.to_lowercase()) {
                        return Err(format!(
                            "Nome OPC DA duplicado no módulo {}: {}.",
                            module.name, variable.name
                        ));
                    }
                    if variable.initial_value.clone().for_type(variable.data_type).is_none()
                    {
                        return Err(format!(
                            "Valor inicial incompatível com o tipo de {}.",
                            variable.name
                        ));
                    }
                    if let Some(source) = &variable.source {
                        if source.module_id.is_empty() || source.module_id.len() > 80 || source.item_id.is_empty()
                            || source.item_id.len() > 1024 || source.item_id.chars().any(char::is_control)
                            || !(15..=10800).contains(&source.stale_after_s) {
                            return Err("Vínculo de origem OPC DA inválido.".into());
                        }
                        match (source.source_data_type, source.source_conversion, variable.data_type) {
                            (None, None, _) => {}
                            (Some(SourceDataType::Float), Some(SourceConversion::FloatToDouble), DataType::Double) => {}
                            (Some(SourceDataType::Auto), Some(SourceConversion::Automatic), _) => {}
                            _ => return Err("Conversão/tipo de origem OPC DA não suportado.".into()),
                        }
                    }
                    if variable.description.len() > 4096
                        || variable.node_id.len() > 1024
                        || variable.node_id.chars().any(char::is_control)
                    {
                        return Err("Descrição ou NodeId excede o limite permitido.".into());
                    }
                }
            }
        }
        if count > 10_000 {
            return Err("O laboratório permite até 10000 variáveis.".into());
        }
        Ok(())
    }
    pub fn published_variables(&self) -> Vec<PublishedVariable> {
        self.connections
            .iter()
            .flat_map(|c| &c.modules)
            .flat_map(|m| {
                m.variables.iter().map(move |v| PublishedVariable {
                    item_id: v.item_id.clone().unwrap_or_else(|| item_id(&m.name, &v.name)),
                    variable: v.clone(),
                })
            })
            .collect()
    }
    pub fn revision(&self) -> String {
        // Change detector only, not a cryptographic integrity guarantee.
        let bytes = serde_json::to_vec(self).expect("validated serializable project");
        let h = bytes.iter().fold(0xcbf29ce484222325u64, |h, b| {
            (h ^ u64::from(*b)).wrapping_mul(0x100000001b3)
        });
        format!("{h:016x}")
    }
    pub fn load(path: &Path) -> Result<Self, String> {
        if std::fs::metadata(path).map_err(|e| e.to_string())?.len() > MAX_CONFIG_BYTES {
            return Err("Arquivo de configuração excede 1 MiB.".into());
        }
        let project: Self =
            serde_json::from_slice(&std::fs::read(path).map_err(|e| e.to_string())?)
                .map_err(|e| format!("Configuração JSON inválida: {e}"))?;
        project.validate()?;
        Ok(project)
    }
    pub fn save(&self, path: &Path) -> Result<(), String> {
        self.validate()?;
        let bytes = serde_json::to_vec_pretty(self).map_err(|e| e.to_string())?;
        if bytes.len() as u64 > MAX_CONFIG_BYTES {
            return Err("Configuração excede 1 MiB.".into());
        }
        let temp = path.with_extension(format!("json.tmp-{}", std::process::id()));
        let mut file = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&temp)
            .map_err(|e| e.to_string())?;
        let result = (|| {
            file.write_all(&bytes)?;
            file.sync_all()?;
            drop(file);
            replace_file(&temp, path)
        })();
        if result.is_err() {
            let _ = std::fs::remove_file(&temp);
        }
        result.map_err(|e| e.to_string())
    }
}
#[cfg(not(windows))]
pub fn replace_file(from: &Path, to: &Path) -> std::io::Result<()> {
    std::fs::rename(from, to)
}
#[cfg(windows)]
pub fn replace_file(from: &Path, to: &Path) -> std::io::Result<()> {
    use std::os::windows::ffi::OsStrExt;
    #[link(name = "kernel32")]
    unsafe extern "system" {
        fn MoveFileExW(from: *const u16, to: *const u16, flags: u32) -> i32;
    }
    let a: Vec<u16> = from.as_os_str().encode_wide().chain(Some(0)).collect();
    let b: Vec<u16> = to.as_os_str().encode_wide().chain(Some(0)).collect();
    if unsafe { MoveFileExW(a.as_ptr(), b.as_ptr(), 1 | 8) } == 0 {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(())
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Sample {
    #[serde(default)]
    pub hresult: i32,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_module_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_vartype: Option<u16>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub published_vartype: Option<u16>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_data_type: Option<SourceDataType>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_conversion: Option<SourceConversion>,
    pub item_id: String,
    pub data_type: DataType,
    pub value: Option<SimValue>,
    pub quality: u16,
    pub source_timestamp_filetime: Option<u64>,
}
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct ServerSnapshot {
    #[serde(default)]
    pub config_sha256: Option<String>,
    #[serde(default)]
    pub machine_id: Option<String>,
    #[serde(default)]
    pub configuration_revision: Option<u64>,
    pub pid: u32,
    pub project_name: String,
    pub config_revision: String,
    pub source_available: bool,
    pub samples: Vec<Sample>,
}
pub fn quality_label(q: u16) -> &'static str {
    match q & 0xc0 {
        0xc0 => "Boa",
        0x40 => "Incerta",
        _ => "Ruim",
    }
}

/// Reads the actual cache exported by the laboratory server, on loopback only.
pub fn fetch_snapshot(port: u16) -> Result<ServerSnapshot, String> {
    let address = std::net::SocketAddr::from(([127, 0, 0, 1], port));
    let mut stream = std::net::TcpStream::connect_timeout(&address, Duration::from_millis(600))
        .map_err(|e| format!("Servidor local indisponível: {e}"))?;
    stream
        .set_read_timeout(Some(Duration::from_millis(1000)))
        .map_err(|e| e.to_string())?;
    stream
        .set_write_timeout(Some(Duration::from_millis(1000)))
        .map_err(|e| e.to_string())?;
    stream
        .write_all(b"GET /v1/snapshot HTTP/1.0\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        .map_err(|e| e.to_string())?;
    let mut bytes = Vec::new();
    stream
        .take(MAX_CONFIG_BYTES * 4 + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > MAX_CONFIG_BYTES * 4 {
        return Err("Resposta do servidor excede o limite.".into());
    }
    let boundary = bytes
        .windows(4)
        .position(|w| w == b"\r\n\r\n")
        .ok_or("Resposta local inválida")?;
    if !bytes.starts_with(b"HTTP/1.0 200 ") && !bytes.starts_with(b"HTTP/1.1 200 ") {
        return Err("Servidor local recusou a consulta.".into());
    }
    serde_json::from_slice(&bytes[boundary + 4..])
        .map_err(|e| format!("Resposta local inválida: {e}"))
}

/// One complete per-source batch. Never contains credentials or a write operation.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SourceSample {
    pub item_id: String,
    pub source_item_id: String,
    pub data_type: DataType,
    pub value: Option<SimValue>,
    pub quality: u16,
    pub hresult: i32,
    pub source_timestamp_filetime: Option<u64>,
    #[serde(default)]
    pub source_vartype: Option<u16>,
    #[serde(default)]
    pub published_vartype: Option<u16>,
    #[serde(default)]
    pub source_data_type: Option<SourceDataType>,
    #[serde(default)]
    pub source_conversion: Option<SourceConversion>,
}

/// Independent dynamic catalog used only when project.json is explicitly present.
#[derive(Clone)]
pub struct ProjectRuntime {
    pub config_sha256: Option<String>,
    pub loaded_deployment: Option<Deployment>,
    pub project: Project,
    samples: BTreeMap<String, Sample>,
    available: bool,
    source_sessions: BTreeMap<String, (u64, u64, std::time::Instant)>,
    next_source_session: u64,
}
impl ProjectRuntime {
    pub fn new(project: Project) -> Result<Self, String> {
        project.validate()?;
        let samples = project
            .published_variables()
            .into_iter()
            .map(|v| {
                (
                    v.item_id.clone(),
                    Sample {
                        hresult: 0,
                        source_module_id: v.variable.source.as_ref().map(|s| s.module_id.clone()),
                        source_vartype: None,
                        published_vartype: None,
                        source_data_type: None,
                        source_conversion: None,
                        item_id: v.item_id,
                        data_type: v.variable.data_type,
                        value: None,
                        quality: 0x20,
                        source_timestamp_filetime: None,
                    },
                )
            })
            .collect();
        Ok(Self {
            config_sha256: None,
            loaded_deployment: None,
            project,
            samples,
            available: true,
            source_sessions: BTreeMap::new(),
            next_source_session: 0,
        })
    }
    /// Only simulated values and the deployment revision may change while clients
    /// hold groups and handles. Validate fully before mutating the shared cache.
    pub fn apply_value_revision(&mut self, next: Project, digest: String, timestamp: Option<u64>) -> Result<(), String> {
        next.validate()?;
        let old_deployment = self.project.deployment.as_ref().ok_or("Configuração ativa sem identidade de destino")?;
        let new_deployment = next.deployment.as_ref().ok_or("Configuração nova sem identidade de destino")?;
        if old_deployment.machine_id != new_deployment.machine_id {
            return Err("Identidade da máquina divergente".into());
        }
        if new_deployment.configuration_revision < old_deployment.configuration_revision {
            return Err("Revisão antiga recusada".into());
        }
        let mut expected = self.project.clone();
        expected.deployment = next.deployment.clone();
        for (old_c, new_c) in expected.connections.iter_mut().zip(&next.connections) {
            for (old_m, new_m) in old_c.modules.iter_mut().zip(&new_c.modules) {
                for (old_v, new_v) in old_m.variables.iter_mut().zip(&new_m.variables) {
                    old_v.initial_value = new_v.initial_value.clone();
                }
            }
        }
        if expected != next {
            return Err("Alteração estrutural: reinício exigido.".into());
        }
        if new_deployment.configuration_revision == old_deployment.configuration_revision {
            if self.project != next {
                return Err("Revisão já usada por outra configuração".into());
            }
            if self.config_sha256.as_ref().is_some_and(|s| s != &digest) {
                return Err("Revisão já usada por outra configuração".into());
            }
            // Idempotent re-application of identical revision and content
            return Ok(());
        }
        self.source_sessions.clear();
        self.project = next;
        self.loaded_deployment = self.project.deployment.clone();
        self.config_sha256 = Some(digest);
        self.tick(timestamp);
        Ok(())
    }
    pub fn tick(&mut self, timestamp: Option<u64>) {
        self.expire_sources();
        if !self.available { return; }
        for v in self.project.published_variables() {
            if v.variable.source.is_some() { continue; }
            let sample = self.samples.get_mut(&v.item_id).expect("validated catalog");
            sample.value = v.variable.initial_value.for_type(v.variable.data_type);
            sample.source_timestamp_filetime = timestamp.filter(|t| *t != 0);
            sample.quality = if sample.source_timestamp_filetime.is_some() {
                0xc0
            } else {
                0x40
            };
        }
    }
    pub fn check_source_target(&self, machine: &str, revision: u64, digest: &str, module: &str) -> Result<(), String> {
        let active = self.loaded_deployment.as_ref().ok_or("Destino sem configuração confirmada")?;
        if active.machine_id != machine || active.configuration_revision != revision
            || self.config_sha256.as_deref() != Some(digest) { return Err("Destino/revisão/digest divergente".into()); }
        if !self.project.published_variables().iter().any(|v| v.variable.source.as_ref().is_some_and(|s| s.module_id == module)) {
            return Err("Origem não pertence ao destino".into());
        }
        Ok(())
    }
    pub fn start_source_session(&mut self, module: &str) -> u64 {
        self.next_source_session += 1;
        self.source_sessions.insert(module.into(), (self.next_source_session, 0, std::time::Instant::now()));
        for sample in self.samples.values_mut().filter(|s| s.source_module_id.as_deref() == Some(module)) {
            sample.quality = 8;
            sample.hresult = 0x800706bau32 as i32;
        }
        self.next_source_session
    }
    pub fn apply_source_samples(&mut self, module: &str, session: u64, sequence: u64, incoming: Vec<SourceSample>) -> Result<(), String> {
        let (current, last, _) = self.source_sessions.get(module).ok_or("Sessão de coleta ausente")?;
        if *current != session || sequence <= *last { return Err("Sessão substituída ou sequência antiga".into()); }
        let expected: BTreeMap<_, _> = self.project.published_variables().into_iter()
            .filter(|v| v.variable.source.as_ref().is_some_and(|s| s.module_id == module))
            .map(|v| (v.item_id, v.variable)).collect();
        if expected.len() != incoming.len() { return Err("Catálogo parcial/divergente".into()); }
        let mut seen = BTreeSet::new();
        let mut candidate = self.samples.clone();
        for source in incoming {
            let variable = expected.get(&source.item_id).ok_or("Item de outra origem/destino")?;
            if !seen.insert(source.item_id.clone()) || variable.source.as_ref().unwrap().item_id != source.source_item_id
                || variable.data_type != source.data_type { return Err("Item ID/tipo/vínculo divergente".into()); }
            let sample = candidate.get_mut(&source.item_id).unwrap();
            sample.hresult = source.hresult;
            sample.source_vartype = source.source_vartype;
            sample.published_vartype = source.published_vartype;
            sample.source_data_type = source.source_data_type;
            sample.source_conversion = source.source_conversion;
            if source.hresult < 0 {
                sample.quality = source.quality & !0xc0; // Retain last known value/time only as Bad.
                continue;
            }
            let value = source.value.ok_or("Amostra sem valor válido")?
                .for_type(variable.data_type).ok_or("Tipo de valor incompatível")?;
            let binding = variable.source.as_ref().expect("source variable");
            let expected_published_vt = variable.data_type.vartype();
            let automatic = binding.source_data_type == Some(SourceDataType::Auto)
                && binding.source_conversion == Some(SourceConversion::Automatic);
            let expected_source_vt = match binding.source_data_type {
                Some(SourceDataType::Float) => 4,
                Some(SourceDataType::Boolean) => 11,
                Some(SourceDataType::Double) => 5,
                Some(SourceDataType::Integer) => 3,
                Some(SourceDataType::String) => 8,
                Some(SourceDataType::Auto) => 0,
                None => expected_published_vt,
            };
            let valid_source_vt = if automatic {
                source.source_vartype.is_some_and(is_supported_scalar_vartype)
            } else {
                source.source_vartype.is_none_or(|vt| vt == expected_source_vt)
                    && (binding.source_conversion.is_none() || source.source_vartype == Some(expected_source_vt))
            };
            if source.source_data_type != binding.source_data_type
                || source.source_conversion != binding.source_conversion
                || !valid_source_vt
                || source.published_vartype.is_some_and(|vt| vt != expected_published_vt)
                || (binding.source_conversion.is_some() && source.published_vartype != Some(expected_published_vt)) {
                return Err("VARTYPE de origem incompatível".into());
            }
            sample.value = Some(value);
            sample.source_timestamp_filetime = source.source_timestamp_filetime.filter(|t| *t > 0 && *t <= 2650467743999999999);
            sample.quality = if source.quality & 0xc0 == 0xc0 && sample.source_timestamp_filetime.is_none() {
                (source.quality & !0xc0) | 0x40
            } else { source.quality };
        }
        self.samples = candidate;
        self.source_sessions.insert(module.into(), (session, sequence, std::time::Instant::now()));
        Ok(())
    }
    pub fn expire_sources(&mut self) {
        for variable in self.project.published_variables() {
            if let Some(source) = variable.variable.source {
                let stale = self.source_sessions.get(&source.module_id).is_none_or(|(_, _, received)| received.elapsed().as_secs() >= source.stale_after_s);
                if stale {
                    let sample = self.samples.get_mut(&variable.item_id).unwrap();
                    sample.quality = 8;
                    sample.hresult = 0x800706bau32 as i32;
                }
            }
        }
    }
    pub fn set_source_available(&mut self, available: bool) {
        self.available = available;
        if !available {
            for sample in self.samples.values_mut() {
                sample.quality = 0x08;
            }
        }
    }
    pub fn sample(&self, id: &str) -> Option<Sample> {
        self.samples.get(id).cloned()
    }
    pub fn snapshot(&self) -> ServerSnapshot {
        ServerSnapshot {
            config_sha256: self.config_sha256.clone(),
            machine_id: self.loaded_deployment.as_ref().map(|d| d.machine_id.clone()),
            configuration_revision: self.loaded_deployment.as_ref().map(|d| d.configuration_revision),
            pid: std::process::id(),
            project_name: self.project.name.clone(),
            config_revision: self.project.revision(),
            source_available: self.available,
            samples: self.samples.values().cloned().collect(),
        }
    }
    pub fn reject_write(&self) -> Result<(), String> {
        Err("OPC_E_BADRIGHTS: variável somente leitura".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn example() -> Project {
        let mut p = Project::default();
        p.connections[0].modules.push(Module {
            name: "Processo".into(),
            variables: vec![
                Variable {
                    source: None,
                    item_id: None,
                    name: "Nivel".into(),
                    data_type: DataType::Double,
                    description: "".into(),
                    node_id: "".into(),
                    initial_value: SimValue::Double(485.7),
                },
                Variable {
                    source: None,
                    item_id: None,
                    name: "Rodando".into(),
                    data_type: DataType::Boolean,
                    description: "".into(),
                    node_id: "".into(),
                    initial_value: SimValue::Boolean(true),
                },
            ],
        });
        p
    }
    fn source_runtime() -> ProjectRuntime {
        let mut project = example();
        project.deployment = Some(Deployment { machine_id: "destination-a".into(), configuration_revision: 10 });
        project.connections[0].modules[0].variables[0].source = Some(SourceBinding {
            module_id: "origin-a".into(), item_id: "Source.Exact:Level".into(), stale_after_s: 30,
            source_data_type: None, source_conversion: None,
        });
        let mut r = ProjectRuntime::new(project).unwrap();
        r.loaded_deployment = r.project.deployment.clone(); r.config_sha256 = Some("digest".into());r
    }
    fn source_sample(value: f64) -> SourceSample {
        SourceSample { item_id: "Processo.Nivel".into(), source_item_id: "Source.Exact:Level".into(),
            data_type: DataType::Double, value: Some(SimValue::Double(value)), quality: 0xc0,
            source_timestamp_filetime: Some(12345678), hresult: 0, source_vartype: Some(5),
            published_vartype: None, source_data_type: None, source_conversion: None }
    }
    #[test]
    fn explicit_float_to_double_source_conversion_is_verified_and_preserved() {
        let mut project = example();
        project.deployment = Some(Deployment { machine_id: "destination-a".into(), configuration_revision: 11 });
        project.connections[0].modules[0].variables[0].source = Some(SourceBinding {
            module_id: "origin-a".into(), item_id: "ExampleChannel.Device.Ramp".into(),
            stale_after_s: 30, source_data_type: Some(SourceDataType::Float),
            source_conversion: Some(SourceConversion::FloatToDouble),
        });
        assert!(project.validate().is_ok());
        let mut runtime = ProjectRuntime::new(project).unwrap();
        runtime.loaded_deployment = runtime.project.deployment.clone();
        runtime.config_sha256 = Some("digest".into());
        let session = runtime.start_source_session("origin-a");
        let converted = SourceSample {
            item_id: "Processo.Nivel".into(),
            source_item_id: "ExampleChannel.Device.Ramp".into(),
            data_type: DataType::Double,
            value: Some(SimValue::Double(61.75)), quality: 0xc0,
            source_timestamp_filetime: Some(12345678), hresult: 0,
            source_vartype: Some(4), published_vartype: Some(5),
            source_data_type: Some(SourceDataType::Float),
            source_conversion: Some(SourceConversion::FloatToDouble),
        };
        runtime.apply_source_samples("origin-a", session, 1, vec![converted.clone()]).unwrap();
        let sample = runtime.sample("Processo.Nivel").unwrap();
        assert_eq!(sample.value, Some(SimValue::Double(61.75)));
        assert_eq!(sample.source_vartype, Some(4));
        assert_eq!(sample.published_vartype, Some(5));
        assert_eq!(sample.source_data_type, Some(SourceDataType::Float));
        assert_eq!(sample.source_conversion, Some(SourceConversion::FloatToDouble));
        assert_eq!(sample.quality, 0xc0);
        assert_eq!(sample.source_timestamp_filetime, Some(12345678));

        let mut wrong_variant = converted;
        wrong_variant.source_vartype = Some(5);
        assert!(runtime.apply_source_samples("origin-a", session, 2, vec![wrong_variant]).is_err());
        assert_eq!(runtime.sample("Processo.Nivel").unwrap(), sample);
    }
    #[test]
    fn same_type_boolean_source_uses_implicit_binding_metadata() {
        let mut project = example();
        project.deployment = Some(Deployment { machine_id: "destination-a".into(), configuration_revision: 12 });
        project.connections[0].modules[0].variables[1].source = Some(SourceBinding {
            module_id: "origin-a".into(), item_id: "Source.Digital".into(), stale_after_s: 30,
            source_data_type: None, source_conversion: None,
        });
        let mut runtime = ProjectRuntime::new(project).unwrap();
        runtime.loaded_deployment = runtime.project.deployment.clone();
        runtime.config_sha256 = Some("digest".into());
        let session = runtime.start_source_session("origin-a");
        let sample = SourceSample {
            item_id: "Processo.Rodando".into(), source_item_id: "Source.Digital".into(),
            data_type: DataType::Boolean, value: Some(SimValue::Boolean(true)), quality: 0x40,
            source_timestamp_filetime: None, hresult: 0, source_vartype: Some(11),
            published_vartype: Some(11), source_data_type: None, source_conversion: None,
        };
        runtime.apply_source_samples("origin-a", session, 1, vec![sample.clone()]).unwrap();
        let current = runtime.sample("Processo.Rodando").unwrap();
        assert_eq!(current.value, Some(SimValue::Boolean(true)));
        assert_eq!(current.quality, 0x40);
        assert_eq!(current.source_timestamp_filetime, None);
        assert_eq!(current.source_vartype, Some(11));

        // Redundant metadata that disagrees with the serialized binding is
        // rejected. The relay must normalize same-type metadata to None.
        let mut redundant = sample;
        redundant.source_data_type = Some(SourceDataType::Boolean);
        assert!(runtime.apply_source_samples("origin-a", session, 2, vec![redundant]).is_err());
        assert_eq!(runtime.sample("Processo.Rodando").unwrap(), current);
    }
    #[test]
    fn external_source_is_never_replaced_by_simulated_tick_and_preserves_timestamp() {
        let mut r = source_runtime();r.tick(Some(999));
        assert_eq!(r.sample("Processo.Nivel").unwrap().value,None);
        let session=r.start_source_session("origin-a");
        r.apply_source_samples("origin-a",session,1,vec![source_sample(7.)]).unwrap();
        r.tick(Some(999999));let s=r.sample("Processo.Nivel").unwrap();
        assert_eq!(s.value,Some(SimValue::Double(7.)));assert_eq!(s.quality,0xc0);
        assert_eq!(s.source_timestamp_filetime,Some(12345678));
        assert_eq!(r.sample("Processo.Rodando").unwrap().quality,0xc0);
    }
    #[test]
    fn external_batches_are_atomic_fenced_and_isolated() {
        let mut r=source_runtime();let id=r.start_source_session("origin-a");
        r.apply_source_samples("origin-a",id,1,vec![source_sample(7.)]).unwrap();
        let before=r.sample("Processo.Nivel").unwrap();
        assert!(r.apply_source_samples("origin-a",id,1,vec![source_sample(8.)]).is_err());
        let mut wrong=source_sample(8.);wrong.source_item_id="different".into();
        assert!(r.apply_source_samples("origin-a",id,2,vec![wrong]).is_err());
        let mut wrong=source_sample(8.);wrong.value=Some(SimValue::Boolean(true));
        assert!(r.apply_source_samples("origin-a",id,2,vec![wrong]).is_err());
        assert!(r.apply_source_samples("origin-a",id,2,vec![]).is_err());
        assert_eq!(r.sample("Processo.Nivel").unwrap(),before);
        let new=r.start_source_session("origin-a");assert_ne!(id,new);
        assert!(r.apply_source_samples("origin-a",id,2,vec![source_sample(8.)]).is_err());
        assert!(r.check_source_target("other",10,"digest","origin-a").is_err());
        assert!(r.check_source_target("destination-a",9,"digest","origin-a").is_err());
        assert!(r.check_source_target("destination-a",10,"wrong","origin-a").is_err());
        assert!(r.check_source_target("destination-a",10,"digest","other").is_err());
    }
    #[test]
    fn source_failure_expiry_and_recovery_do_not_invent_good_or_timestamps() {
        let mut r=source_runtime();let id=r.start_source_session("origin-a");
        r.apply_source_samples("origin-a",id,1,vec![source_sample(7.)]).unwrap();
        let mut failure=source_sample(0.);failure.value=None;failure.hresult=0xc0040007u32 as i32;failure.quality=0;failure.source_timestamp_filetime=None;
        r.apply_source_samples("origin-a",id,2,vec![failure]).unwrap();
        let s=r.sample("Processo.Nivel").unwrap();assert_eq!(s.value,Some(SimValue::Double(7.)));assert_eq!(s.source_timestamp_filetime,Some(12345678));assert_eq!(s.quality&0xc0,0);assert!(s.hresult<0);
        let mut missing=source_sample(8.);missing.source_timestamp_filetime=None;
        r.apply_source_samples("origin-a",id,3,vec![missing]).unwrap();
        let s=r.sample("Processo.Nivel").unwrap();assert_eq!(s.source_timestamp_filetime,None);assert_eq!(s.quality&0xc0,0x40);
        r.apply_source_samples("origin-a",id,4,vec![source_sample(9.)]).unwrap();
        r.source_sessions.get_mut("origin-a").unwrap().2=std::time::Instant::now()-Duration::from_secs(31);
        r.expire_sources();let stale=r.sample("Processo.Nivel").unwrap();assert_eq!(stale.quality,8);assert_eq!(stale.source_timestamp_filetime,Some(12345678));
        r.apply_source_samples("origin-a",id,5,vec![source_sample(10.)]).unwrap();assert_eq!(r.sample("Processo.Nivel").unwrap().quality,0xc0);
    }
    #[test]
    fn validation_and_item_ids() {
        let mut p = example();
        assert!(p.validate().is_ok());
        assert_eq!(p.published_variables()[0].item_id, "Processo.Nivel");
        p.connections[0].modules[0].variables[1].name = "nivel".into();
        assert!(p.validate().is_err());
        p.connections[0].modules[0].variables[1].name = "Rodando".into();
        p.connections[0].modules[0].variables[1].initial_value = SimValue::Double(1.0);
        assert!(p.validate().is_err());
    }
    #[test]
    fn explicit_ids_preserve_labels_and_reject_duplicates() {
        let mut p = example();
        p.connections[0].modules[0].name = "Módulo / Pasta".into();
        let vars = &mut p.connections[0].modules[0].variables;
        vars[0].item_id = Some("Destino.Exato.Numeric".into());
        vars[1].item_id = Some("Destino.Exato.Boolean".into());
        p.deployment = Some(Deployment { machine_id: "target-a".into(), configuration_revision: 8 });
        assert!(p.validate().is_ok());
        assert_eq!(p.published_variables()[0].item_id, "Destino.Exato.Numeric");
        let decoded: Project = serde_json::from_str(&serde_json::to_string(&p).unwrap()).unwrap();
        assert_eq!(decoded, p);
        p.connections[0].modules[0].variables[1].item_id = Some("Destino.Exato.Numeric".into());
        assert!(p.validate().is_err());
        p.connections[0].modules[0].variables[1].item_id = Some("bad\nitem".into());
        assert!(p.validate().is_err());
    }
    #[test]
    fn configuration_receipt_metadata_is_not_a_good_sample() {
        let p = example();
        let mut runtime = ProjectRuntime::new(p).unwrap();
        runtime.config_sha256 = Some("test-digest".into());
        runtime.loaded_deployment = Some(Deployment { machine_id: "target-a".into(), configuration_revision: 8 });
        let snap = runtime.snapshot();
        assert_eq!(snap.machine_id.as_deref(), Some("target-a"));
        assert_eq!(snap.configuration_revision, Some(8));
        assert_eq!(snap.config_sha256.as_deref(), Some("test-digest"));
        assert!(snap.samples.iter().all(|s| s.quality != 0xc0 && s.source_timestamp_filetime.is_none()));
    }
    #[test]
    fn value_revision_preserves_samples_and_rejects_structural_changes() {
        let mut p = example();
        p.deployment = Some(Deployment { machine_id: "machine-a".into(), configuration_revision: 1 });
        let mut r = ProjectRuntime::new(p.clone()).unwrap();
        r.config_sha256 = Some("old".into());
        r.loaded_deployment = p.deployment.clone();
        r.tick(Some(123));
        let mut next = p.clone();
        next.deployment.as_mut().unwrap().configuration_revision = 2;
        next.connections[0].modules[0].variables[0].initial_value = SimValue::Double(7.0);
        r.apply_value_revision(next.clone(), "new".into(), Some(456)).unwrap();
        let s = r.sample("Processo.Nivel").unwrap();
        assert_eq!(s.value, Some(SimValue::Double(7.0)));
        assert_eq!(s.quality, 0xc0);
        assert_eq!(s.source_timestamp_filetime, Some(456));
        assert_eq!(r.snapshot().configuration_revision, Some(2));
        let mut structural = next.clone();
        structural.deployment.as_mut().unwrap().configuration_revision = 3;
        structural.connections[0].modules[0].variables[0].name = "Other".into();
        assert!(r.apply_value_revision(structural, "bad".into(), Some(789)).is_err());
        assert_eq!(r.sample("Processo.Nivel").unwrap().value, Some(SimValue::Double(7.0)));
        assert_eq!(r.snapshot().config_sha256.as_deref(), Some("new"));
        let mut invalid = next.clone();
        invalid.connections[0].modules[0].variables[0].initial_value = SimValue::Boolean(true);
        assert!(r.apply_value_revision(invalid, "invalid".into(), Some(789)).is_err());
        r.set_source_available(false);
        next.deployment.as_mut().unwrap().configuration_revision = 3;
        next.connections[0].modules[0].variables[0].initial_value = SimValue::Double(8.0);
        r.apply_value_revision(next, "offline".into(), Some(999)).unwrap();
        assert_eq!(r.sample("Processo.Nivel").unwrap().quality, 0x08);
        assert_eq!(r.sample("Processo.Nivel").unwrap().source_timestamp_filetime, Some(456));
    }
    #[test]
    fn concurrent_reads_see_whole_value_revision_and_stale_revisions_fail() {
        use std::sync::{Arc, Mutex};
        let mut p = example();
        p.deployment = Some(Deployment { machine_id: "machine-a".into(), configuration_revision: 1 });
        let mut runtime = ProjectRuntime::new(p.clone()).unwrap();
        runtime.config_sha256 = Some("one".into());
        runtime.loaded_deployment = p.deployment.clone();
        runtime.tick(Some(100));
        let shared = Arc::new(Mutex::new(runtime));
        let other = shared.clone();
        let reader = std::thread::spawn(move || {
            for _ in 0..2000 {
                let locked = other.lock().unwrap();
                let numeric = locked.sample("Processo.Nivel").unwrap().value;
                let boolean = locked.sample("Processo.Rodando").unwrap().value;
                assert!(matches!((numeric, boolean),
                    (Some(SimValue::Double(485.7)), Some(SimValue::Boolean(true))) |
                    (Some(SimValue::Double(7.0)), Some(SimValue::Boolean(false)))));
            }
        });
        let mut next = p.clone();
        next.deployment.as_mut().unwrap().configuration_revision = 2;
        next.connections[0].modules[0].variables[0].initial_value = SimValue::Double(7.0);
        next.connections[0].modules[0].variables[1].initial_value = SimValue::Boolean(false);
        shared.lock().unwrap().apply_value_revision(next, "two".into(), Some(200)).unwrap();
        reader.join().unwrap();
        let mut guard = shared.lock().unwrap();
        assert!(guard.apply_value_revision(p, "one".into(), Some(300)).is_err());
        assert_eq!(guard.snapshot().configuration_revision, Some(2));
    }
    #[test]
    fn snapshots_never_invent_good_or_timestamps() {
        let mut r = ProjectRuntime::new(example()).unwrap();
        assert_eq!(r.sample("Processo.Nivel").unwrap().quality, 0x20);
        r.tick(None);
        assert_eq!(r.sample("Processo.Nivel").unwrap().quality, 0x40);
        r.tick(Some(123));
        let good = r.sample("Processo.Nivel").unwrap();
        assert_eq!(good.value, Some(SimValue::Double(485.7)));
        r.set_source_available(false);
        r.tick(Some(456));
        let bad = r.sample("Processo.Nivel").unwrap();
        assert_eq!(bad.quality, 0x08);
        assert_eq!(bad.source_timestamp_filetime, Some(123));
        r.set_source_available(true);
        assert_eq!(r.sample("Processo.Nivel").unwrap().quality, 0x08);
        r.tick(Some(789));
        assert_eq!(r.sample("Processo.Nivel").unwrap().quality, 0xc0);
        assert!(r.reject_write().is_err());
    }

    #[test]
    fn structural_changes_are_strictly_rejected_with_restart_required_message() {
        let mut p = example();
        p.deployment = Some(Deployment { machine_id: "machine-a".into(), configuration_revision: 1 });
        let mut r = ProjectRuntime::new(p.clone()).unwrap();
        r.config_sha256 = Some("hash1".into());
        r.loaded_deployment = p.deployment.clone();
        r.tick(Some(100));

        let mut added = p.clone();
        added.deployment.as_mut().unwrap().configuration_revision = 2;
        added.connections[0].modules[0].variables.push(Variable {
            source: None,
            item_id: None,
            name: "NovoValor".into(),
            data_type: DataType::Double,
            description: "".into(),
            node_id: "".into(),
            initial_value: SimValue::Double(12.3),
        });
        let err = r.apply_value_revision(added, "hash_add".into(), Some(101)).unwrap_err();
        assert_eq!(err, "Alteração estrutural: reinício exigido.");

        let mut removed = p.clone();
        removed.deployment.as_mut().unwrap().configuration_revision = 2;
        removed.connections[0].modules[0].variables.pop();
        let err = r.apply_value_revision(removed, "hash_rem".into(), Some(102)).unwrap_err();
        assert_eq!(err, "Alteração estrutural: reinício exigido.");

        let mut renamed = p.clone();
        renamed.deployment.as_mut().unwrap().configuration_revision = 2;
        renamed.connections[0].modules[0].variables[0].name = "NivelModificado".into();
        let err = r.apply_value_revision(renamed, "hash_ren".into(), Some(103)).unwrap_err();
        assert_eq!(err, "Alteração estrutural: reinício exigido.");

        let mut type_changed = p.clone();
        type_changed.deployment.as_mut().unwrap().configuration_revision = 2;
        type_changed.connections[0].modules[0].variables[0].data_type = DataType::Boolean;
        type_changed.connections[0].modules[0].variables[0].initial_value = SimValue::Boolean(false);
        let err = r.apply_value_revision(type_changed, "hash_type".into(), Some(104)).unwrap_err();
        assert_eq!(err, "Alteração estrutural: reinício exigido.");

        assert_eq!(r.snapshot().configuration_revision, Some(1));
        assert_eq!(r.snapshot().config_sha256.as_deref(), Some("hash1"));
        assert_eq!(r.sample("Processo.Nivel").unwrap().value, Some(SimValue::Double(485.7)));
    }

    #[test]
    fn identity_revision_and_digest_conflict_validations() {
        let mut p = example();
        p.deployment = Some(Deployment { machine_id: "target-1".into(), configuration_revision: 5 });
        let mut r = ProjectRuntime::new(p.clone()).unwrap();
        r.config_sha256 = Some("digest-5".into());
        r.loaded_deployment = p.deployment.clone();
        r.tick(Some(500));

        // Machine mismatch rejected
        let mut diff_machine = p.clone();
        diff_machine.deployment.as_mut().unwrap().machine_id = "target-2".into();
        diff_machine.deployment.as_mut().unwrap().configuration_revision = 6;
        let err = r.apply_value_revision(diff_machine, "digest-6".into(), Some(600)).unwrap_err();
        assert_eq!(err, "Identidade da máquina divergente");

        // Older revision rejected
        let mut older = p.clone();
        older.deployment.as_mut().unwrap().configuration_revision = 4;
        let err = r.apply_value_revision(older, "digest-4".into(), Some(600)).unwrap_err();
        assert_eq!(err, "Revisão antiga recusada");

        // Same revision with modified content rejected (conflict)
        let mut conflict_rev = p.clone();
        conflict_rev.connections[0].modules[0].variables[0].initial_value = SimValue::Double(99.0);
        let err = r.apply_value_revision(conflict_rev.clone(), "digest-5".into(), Some(600)).unwrap_err();
        assert_eq!(err, "Revisão já usada por outra configuração");

        // Same revision with different digest rejected
        let err = r.apply_value_revision(p.clone(), "diff-digest".into(), Some(600)).unwrap_err();
        assert_eq!(err, "Revisão já usada por outra configuração");

        // Idempotent: exact same revision, digest, and content accepted without mutation
        assert!(r.apply_value_revision(p.clone(), "digest-5".into(), Some(600)).is_ok());
        assert_eq!(r.sample("Processo.Nivel").unwrap().value, Some(SimValue::Double(485.7)));
    }

    #[test]
    fn save_reload_and_invalid_input_preserves_file() {
        let path =
            std::env::temp_dir().join(format!("opc-project-test-{}.json", std::process::id()));
        let p = example();
        p.save(&path).unwrap();
        assert_eq!(Project::load(&path).unwrap(), p);
        let mut bad = p.clone();
        bad.connections[0].modules[0].name = "".into();
        assert!(bad.save(&path).is_err());
        assert_eq!(Project::load(&path).unwrap(), p);
        let mut changed = p.clone();
        changed.name = "Outro".into();
        changed.save(&path).unwrap();
        assert_eq!(Project::load(&path).unwrap(), changed);
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn rejected_update_leaves_live_snapshot_and_persisted_file_unchanged() {
        let path = std::env::temp_dir().join(format!("opc-trans-test-{}.json", std::process::id()));
        let mut p = example();
        p.deployment = Some(Deployment {
            machine_id: "m1".into(),
            configuration_revision: 5,
        });
        p.save(&path).unwrap();
        let initial_file_bytes = std::fs::read(&path).unwrap();

        let mut r = ProjectRuntime::new(p.clone()).unwrap();
        r.config_sha256 = Some("init-digest".into());
        r.loaded_deployment = p.deployment.clone();
        r.tick(Some(100));
        let snap_before = r.snapshot();

        // 1. Rejected structural change: adding a module
        let mut structural = p.clone();
        structural.deployment = Some(Deployment {
            machine_id: "m1".into(),
            configuration_revision: 6,
        });
        structural.connections[0].modules.push(Module {
            name: "ModuloNovo".into(),
            variables: vec![],
        });
        let mut candidate = r.clone();
        let err = candidate.apply_value_revision(structural, "digest-struct".into(), Some(200)).unwrap_err();
        assert_eq!(err, "Alteração estrutural: reinício exigido.");
        assert_eq!(r.snapshot().config_revision, snap_before.config_revision);
        assert_eq!(std::fs::read(&path).unwrap(), initial_file_bytes);

        // 2. Rejected stale/conflicting revision (same rev 5 with different value)
        let mut conflict = p.clone();
        conflict.connections[0].modules[0].variables[0].initial_value = SimValue::Double(999.0);
        let mut candidate2 = r.clone();
        let err2 = candidate2.apply_value_revision(conflict, "init-digest".into(), Some(300)).unwrap_err();
        assert_eq!(err2, "Revisão já usada por outra configuração");
        assert_eq!(r.snapshot().config_revision, snap_before.config_revision);
        assert_eq!(std::fs::read(&path).unwrap(), initial_file_bytes);

        std::fs::remove_file(path).unwrap();
    }
    fn four_type_project() -> Project {
        serde_json::from_value(serde_json::json!({
            "schema_version":1,"name":"Four types","diagnostic_port":49326,
            "connections":[{"name":"Laboratory","modules":[{"name":"Types","variables":[
                {"name":"Real","data_type":"Double","initial_value":7},
                {"name":"Integer","data_type":"Integer","initial_value":-2147483648i64},
                {"name":"Text","data_type":"String","initial_value":"Ação 🚀\u{0}fim"},
                {"name":"Boolean","data_type":"Boolean","initial_value":false}
            ]}]}]
        })).unwrap()
    }

    #[test]
    fn four_output_types_normalize_json_numbers_without_truncation() {
        let project = four_type_project();
        project.validate().unwrap();
        let mut runtime = ProjectRuntime::new(project).unwrap();
        runtime.tick(Some(12345678));
        for (id, kind, expected) in [
            ("Types.Real", DataType::Double, SimValue::Double(7.0)),
            ("Types.Integer", DataType::Integer, SimValue::Integer(i32::MIN)),
            ("Types.Text", DataType::String, SimValue::String("Ação 🚀\u{0}fim".into())),
            ("Types.Boolean", DataType::Boolean, SimValue::Boolean(false)),
        ] {
            let sample=runtime.sample(id).unwrap();
            assert_eq!(sample.data_type, kind);assert_eq!(sample.value, Some(expected));
            assert_eq!(sample.quality,192);assert_eq!(sample.source_timestamp_filetime,Some(12345678));
        }
        assert_eq!(DataType::Integer.vartype(),3);assert_eq!(DataType::String.vartype(),8);
        assert!(SimValue::Double(1.25).for_type(DataType::Integer).is_none());
        assert!(SimValue::Double(2147483648.0).for_type(DataType::Integer).is_none());
        assert!(SimValue::Double(-2147483649.0).for_type(DataType::Integer).is_none());
        assert!(SimValue::Boolean(true).for_type(DataType::Integer).is_none());
        assert!(SimValue::String("7".into()).for_type(DataType::Integer).is_none());
        assert!(SimValue::Double(f64::INFINITY).for_type(DataType::Double).is_none());
    }

    #[test]
    fn string_limit_counts_utf16_units_and_keeps_unicode_and_embedded_nul() {
        assert!(SimValue::String("🚀".repeat(2048)).for_type(DataType::String).is_some());
        assert!(SimValue::String("🚀".repeat(2049)).for_type(DataType::String).is_none());
        assert!(SimValue::String("a".repeat(4097)).for_type(DataType::String).is_none());
        let mut project=four_type_project();
        project.connections[0].modules[0].variables[2].initial_value=SimValue::String("🚀".repeat(2049));
        assert!(project.validate().is_err());
    }

    #[test]
    fn automatic_four_type_batches_are_atomic_and_validate_variant_evidence() {
        let mut project=four_type_project();
        for variable in &mut project.connections[0].modules[0].variables {
            variable.source=Some(SourceBinding{module_id:"source".into(),item_id:variable.name.clone(),
                stale_after_s:30,source_data_type:Some(SourceDataType::Auto),source_conversion:Some(SourceConversion::Automatic)});
        }
        let mut runtime=ProjectRuntime::new(project).unwrap();
        let session=runtime.start_source_session("source");
        let incoming:Vec<SourceSample>=serde_json::from_value(serde_json::json!([
            {"item_id":"Types.Real","source_item_id":"Real","data_type":"Double","value":7,"quality":192,"hresult":0,"source_timestamp_filetime":12345678,"source_vartype":4,"published_vartype":5,"source_data_type":"Auto","source_conversion":"automatic"},
            {"item_id":"Types.Integer","source_item_id":"Integer","data_type":"Integer","value":42,"quality":192,"hresult":0,"source_timestamp_filetime":12345678,"source_vartype":2,"published_vartype":3,"source_data_type":"Auto","source_conversion":"automatic"},
            {"item_id":"Types.Text","source_item_id":"Text","data_type":"String","value":"Ação 🚀\u{0}fim","quality":64,"hresult":0,"source_timestamp_filetime":12345678,"source_vartype":8,"published_vartype":8,"source_data_type":"Auto","source_conversion":"automatic"},
            {"item_id":"Types.Boolean","source_item_id":"Boolean","data_type":"Boolean","value":true,"quality":192,"hresult":0,"source_timestamp_filetime":12345678,"source_vartype":11,"published_vartype":11,"source_data_type":"Auto","source_conversion":"automatic"}
        ])).unwrap();
        runtime.apply_source_samples("source",session,1,incoming.clone()).unwrap();
        assert_eq!(runtime.sample("Types.Real").unwrap().value,Some(SimValue::Double(7.0)));
        assert_eq!(runtime.sample("Types.Integer").unwrap().value,Some(SimValue::Integer(42)));
        assert_eq!(runtime.sample("Types.Text").unwrap().quality,64);
        let before=runtime.snapshot().samples;
        for (index, variant) in [(0,0x2004),(1,7),(2,0),(3,10)] {
            let mut bad=incoming.clone();bad[index].source_vartype=Some(variant);
            assert!(runtime.apply_source_samples("source",session,2,bad).is_err());
            assert_eq!(runtime.snapshot().samples,before);
        }
        let mut wrong=incoming.clone();wrong[2].published_vartype=Some(3);
        assert!(runtime.apply_source_samples("source",session,2,wrong).is_err());
        let mut wrong=incoming.clone();wrong[1].value=Some(SimValue::Double(42.5));
        assert!(runtime.apply_source_samples("source",session,2,wrong).is_err());
        assert_eq!(runtime.snapshot().samples,before);
        let mut failed=incoming.clone();failed[1].hresult=0xc0040004u32 as i32;failed[1].quality=0;failed[1].value=None;
        runtime.apply_source_samples("source",session,2,failed).unwrap();
        let retained=runtime.sample("Types.Integer").unwrap();assert_eq!(retained.value,Some(SimValue::Integer(42)));assert_eq!(retained.quality,0);
        runtime.apply_source_samples("source",session,3,incoming).unwrap();
        assert_eq!(runtime.sample("Types.Integer").unwrap().quality,192);
    }

}

/// Windows 7 native SHA-256 provider; no bundled crypto implementation.
#[cfg(windows)]
pub fn sha256(bytes: &[u8]) -> Result<String, String> {
    use std::ffi::c_void;
    type Handle = *mut c_void;
    #[link(name = "bcrypt")]
    unsafe extern "system" {
        fn BCryptOpenAlgorithmProvider(a: *mut Handle, id: *const u16, implementation: *const u16, flags: u32) -> i32;
        fn BCryptGetProperty(a: Handle, property: *const u16, out: *mut u8, size: u32, result: *mut u32, flags: u32) -> i32;
        fn BCryptCreateHash(a: Handle, h: *mut Handle, object: *mut u8, size: u32, secret: *mut u8, secret_size: u32, flags: u32) -> i32;
        fn BCryptHashData(h: Handle, data: *const u8, size: u32, flags: u32) -> i32;
        fn BCryptFinishHash(h: Handle, out: *mut u8, size: u32, flags: u32) -> i32;
        fn BCryptDestroyHash(h: Handle) -> i32;
        fn BCryptCloseAlgorithmProvider(a: Handle, flags: u32) -> i32;
    }
    fn wide(s: &str) -> Vec<u16> { s.encode_utf16().chain(Some(0)).collect() }
    fn check(s: i32) -> Result<(), String> { if s < 0 { Err(format!("BCrypt status {s:#x}")) } else { Ok(()) } }
    unsafe {
        let mut algorithm = std::ptr::null_mut();
        check(BCryptOpenAlgorithmProvider(&mut algorithm, wide("SHA256").as_ptr(), std::ptr::null(), 0))?;
        let result = (|| {
            let mut size = 0u32; let mut written = 0;
            check(BCryptGetProperty(algorithm, wide("ObjectLength").as_ptr(), (&mut size as *mut u32).cast(), 4, &mut written, 0))?;
            let mut object = vec![0u8; size as usize]; let mut hash = std::ptr::null_mut();
            check(BCryptCreateHash(algorithm, &mut hash, object.as_mut_ptr(), size, std::ptr::null_mut(), 0, 0))?;
            let result = (|| {
                for chunk in bytes.chunks(u32::MAX as usize) { check(BCryptHashData(hash, chunk.as_ptr(), chunk.len() as u32, 0))?; }
                let mut digest = [0u8; 32]; check(BCryptFinishHash(hash, digest.as_mut_ptr(), 32, 0))?;
                Ok(digest.iter().map(|b| format!("{b:02x}")).collect())
            })();
            BCryptDestroyHash(hash); result
        })();
        BCryptCloseAlgorithmProvider(algorithm, 0); result
    }

}
#[cfg(not(windows))]
pub fn sha256(_: &[u8]) -> Result<String, String> { Err("SHA256 requires Windows BCrypt".into()) }
