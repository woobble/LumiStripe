//! Version-1 binary dashboard preview packet encoder.

pub const PREVIEW_MAGIC: [u8; 4] = *b"LSFP";
pub const PREVIEW_VERSION: u8 = 1;
pub const PREVIEW_MAX_OUTPUTS: usize = 2;
pub const PREVIEW_MAX_PIXELS: usize = 4096;
const HEADER_BYTES: usize = 12;

pub fn encode_preview_frame(sequence: u64, outputs: &[Vec<u8>]) -> Result<Vec<u8>, &'static str> {
    if outputs.len() > PREVIEW_MAX_OUTPUTS {
        return Err("preview frame cannot contain more than two outputs");
    }
    let mut packet = Vec::with_capacity(
        HEADER_BYTES + outputs.iter().map(Vec::len).sum::<usize>() + outputs.len() * 4,
    );
    packet.extend_from_slice(&PREVIEW_MAGIC);
    packet.push(PREVIEW_VERSION);
    packet.push(0);
    packet.extend_from_slice(&(sequence as u32).to_le_bytes());
    packet.extend_from_slice(&(outputs.len() as u16).to_le_bytes());
    for output in outputs {
        if output.len() % 4 != 0 {
            return Err("preview output must contain complete RGBA pixels");
        }
        let pixels = output.len() / 4;
        if pixels > PREVIEW_MAX_PIXELS {
            return Err("preview output cannot contain more than 4096 pixels");
        }
        packet.extend_from_slice(&(pixels as u32).to_le_bytes());
        packet.extend_from_slice(output);
    }
    Ok(packet)
}

#[cfg(test)]
mod tests {
    use super::encode_preview_frame;

    #[test]
    fn matches_the_shared_version_one_golden_packet() {
        let packet = encode_preview_frame(
            23,
            &[vec![0xff, 0, 1, 0xff], vec![2, 3, 4, 0x80, 9, 8, 7, 0]],
        )
        .unwrap();
        assert_eq!(
            packet,
            [
                0x4c, 0x53, 0x46, 0x50, 0x01, 0x00, 0x17, 0x00, 0x00, 0x00, 0x02, 0x00, 0x01, 0x00,
                0x00, 0x00, 0xff, 0x00, 0x01, 0xff, 0x02, 0x00, 0x00, 0x00, 0x02, 0x03, 0x04, 0x80,
                0x09, 0x08, 0x07, 0x00,
            ]
        );
    }
}
