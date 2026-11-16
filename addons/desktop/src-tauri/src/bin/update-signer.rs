//! Build-only Minisign signing utility, never included in Alice.app.
use base64::{engine::general_purpose::STANDARD, Engine};
use minisign::{KeyPair, PublicKeyBox, SecretKey, SecretKeyBox, SignatureBox};
use std::{fs::{self, OpenOptions}, io::Write, path::Path, time::{SystemTime, UNIX_EPOCH}};

fn private_write(path: &Path, bytes: &[u8]) -> Result<(), Box<dyn std::error::Error>> {
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)] { use std::os::unix::fs::OpenOptionsExt; options.mode(0o600); }
    let mut file = options.open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}

fn execute() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<String> = std::env::args().collect();
    match args.get(1).map(String::as_str) {
        Some("generate") if args.len() == 4 => {
            let secret = Path::new(&args[2]);
            let public = Path::new(&args[3]);
            if secret.exists() || public.exists() { return Err("refusing to overwrite an existing key".into()); }
            let directory = secret.parent().ok_or("key directory missing")?;
            fs::create_dir_all(directory)?;
            #[cfg(unix)] { use std::os::unix::fs::PermissionsExt; fs::set_permissions(directory, fs::Permissions::from_mode(0o700))?; }
            let KeyPair { pk, sk } = KeyPair::generate_unencrypted_keypair()?;
            private_write(secret, sk.to_box(None)?.to_string().as_bytes())?;
            private_write(public, STANDARD.encode(pk.to_box()?.to_string()).as_bytes())?;
        }
        Some("sign") if args.len() == 7 => {
            let public_text = String::from_utf8(STANDARD.decode(fs::read(&args[3])?)?)?;
            let pk = PublicKeyBox::from_string(&public_text)?.into_public_key()?;
            let sk = SecretKey::from_unencrypted_box(SecretKeyBox::from_string(&fs::read_to_string(&args[2])?)?)?;
            let timestamp = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
            let comment = format!("timestamp:{timestamp}\tfile:{}\tversion:{}", Path::new(&args[4]).file_name().ok_or("file name missing")?.to_string_lossy(), args[6]);
            let signature = minisign::sign(Some(&pk), &sk, fs::File::open(&args[4])?, Some(&comment), None)?;
            minisign::verify(&pk, &signature, fs::File::open(&args[4])?, true, false, false)?;
            private_write(Path::new(&args[5]), STANDARD.encode(signature.into_string()).as_bytes())?;
        }
        Some("verify") if args.len() == 5 => {
            let public_text = String::from_utf8(STANDARD.decode(fs::read(&args[2])?)?)?;
            let signature_text = String::from_utf8(STANDARD.decode(fs::read(&args[4])?)?)?;
            let pk = PublicKeyBox::from_string(&public_text)?.into_public_key()?;
            let sig = SignatureBox::from_string(&signature_text)?;
            minisign::verify(&pk, &sig, fs::File::open(&args[3])?, true, false, false)?;
            let verifier = minisign_verify::PublicKey::decode(&public_text)?;
            let signature = minisign_verify::Signature::decode(&signature_text)?;
            verifier.verify(&fs::read(&args[3])?, &signature, true)?;
        }
        _ => return Err("usage: generate KEY PUBLIC | sign KEY PUBLIC FILE SIG VERSION | verify PUBLIC FILE SIG".into()),
    }
    println!("Signing operation completed.");
    Ok(())
}

fn main() {
    if execute().is_err() {
        eprintln!("Signing operation failed; key files were not printed. Check input paths and key availability.");
        std::process::exit(1);
    }
}
