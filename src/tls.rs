use rcgen::generate_simple_self_signed;
use sha2::{Digest, Sha256};
use std::path::Path;
use tracing::info;

/// Ensure TLS certificate and key files exist, generating self-signed ones if needed.
/// Returns Ok(()) on success, Err on failure.
pub fn ensure_tls_cert(cert_path: &Path, key_path: &Path) -> std::io::Result<()> {
    if cert_path.exists() && key_path.exists() {
        info!("[TLS] Using existing cert at {}", cert_path.display());
        return Ok(());
    }

    info!("[TLS] Generating self-signed certificate...");

    // Create parent directories
    if let Some(parent) = cert_path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    if let Some(parent) = key_path.parent() {
        std::fs::create_dir_all(parent)?;
    }

    // Generate self-signed certificate for localhost and common names
    let subject_alt_names = vec!["localhost".to_string(), "127.0.0.1".to_string()];
    let certified_key = generate_simple_self_signed(subject_alt_names).map_err(|e| {
        std::io::Error::new(
            std::io::ErrorKind::Other,
            format!("Failed to generate TLS certificate: {}", e),
        )
    })?;

    let cert_pem = certified_key.cert.pem();
    let key_pem = certified_key.key_pair.serialize_pem();

    // Write cert and key
    std::fs::write(cert_path, &cert_pem)?;
    std::fs::write(key_path, &key_pem)?;

    // chmod 0600 on key file (Unix only)
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(key_path, std::fs::Permissions::from_mode(0o600))?;
    }

    let fingerprint = sha256_fingerprint(&cert_pem);
    info!("[TLS] Certificate fingerprint: {}", fingerprint);
    info!("[TLS] Cert written to {}", cert_path.display());
    info!("[TLS] Key written to {}", key_path.display());

    Ok(())
}

fn sha256_fingerprint(cert_pem: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(cert_pem.as_bytes());
    let hash = hasher.finalize();
    format!("sha256:{}", hex::encode(hash))
}
