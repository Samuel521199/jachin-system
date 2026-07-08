//! Native WAV playback for the companion voice pipeline.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

static PLAYING: AtomicBool = AtomicBool::new(false);
static PLAY_LOCK: Mutex<()> = Mutex::new(());

#[allow(dead_code)]
pub fn is_playing() -> bool {
    PLAYING.load(Ordering::Relaxed)
}

/// Stop current playback immediately. Used by barge-in.
pub fn stop_playback_sync() {
    #[cfg(target_os = "windows")]
    unsafe {
        let _ = winmm_play_sound_w(std::ptr::null(), std::ptr::null_mut(), SND_PURGE | SND_SYNC);
    }
    PLAYING.store(false, Ordering::Relaxed);
}

pub fn play_wav_bytes_sync(bytes: &[u8]) -> Result<(), String> {
    if bytes.len() < 44 {
        return Err("WAV payload too short".into());
    }

    let _guard = PLAY_LOCK
        .lock()
        .map_err(|_| "voice playback lock poisoned".to_string())?;
    stop_playback_sync();

    #[cfg(target_os = "windows")]
    {
        play_wav_bytes_sync_windows(bytes)
    }

    #[cfg(not(target_os = "windows"))]
    {
        let _ = bytes;
        Err("native companion WAV playback is only implemented on Windows".into())
    }
}

#[cfg(target_os = "windows")]
fn play_wav_bytes_sync_windows(bytes: &[u8]) -> Result<(), String> {
    use std::ffi::OsStr;
    use std::os::windows::ffi::OsStrExt;

    let tmp = std::env::temp_dir().join(format!("jachin_voice_{}.wav", uuid::Uuid::new_v4()));
    std::fs::write(&tmp, bytes).map_err(|e| format!("write temp wav: {}", e))?;

    let mut wide: Vec<u16> = OsStr::new(&tmp)
        .encode_wide()
        .chain(std::iter::once(0))
        .collect();

    PLAYING.store(true, Ordering::Relaxed);
    let ok = unsafe {
        winmm_play_sound_w(
            wide.as_mut_ptr(),
            std::ptr::null_mut(),
            SND_FILENAME | SND_NODEFAULT | SND_SYNC,
        )
    };
    PLAYING.store(false, Ordering::Relaxed);
    let _ = std::fs::remove_file(&tmp);
    if ok == 0 {
        Err("winmm PlaySoundW failed".into())
    } else {
        Ok(())
    }
}

#[cfg(target_os = "windows")]
const SND_SYNC: u32 = 0x0000;
#[cfg(target_os = "windows")]
const SND_NODEFAULT: u32 = 0x0002;
#[cfg(target_os = "windows")]
const SND_FILENAME: u32 = 0x0002_0000;
#[cfg(target_os = "windows")]
const SND_PURGE: u32 = 0x0040;

#[cfg(target_os = "windows")]
#[link(name = "winmm")]
extern "system" {
    #[link_name = "PlaySoundW"]
    fn winmm_play_sound_w(
        psz_sound: *const u16,
        hmod: *mut std::ffi::c_void,
        fdw_sound: u32,
    ) -> i32;
}
