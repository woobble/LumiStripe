//! Python-compatible MT19937 draws for effects with seeded random state.

#[derive(Clone, Debug, PartialEq)]
pub struct PythonRandom {
    state: [u32; 624],
    index: usize,
}

impl PythonRandom {
    pub fn new(seed: u32) -> Self {
        let mut value = Self {
            state: [0; 624],
            index: 624,
        };
        value.seed(seed);
        value
    }

    pub fn from_u64(seed: u64) -> Self {
        let low = seed as u32;
        let high = (seed >> 32) as u32;
        let mut value = Self {
            state: [0; 624],
            index: 624,
        };
        if high == 0 {
            value.seed_words(&[low]);
        } else {
            value.seed_words(&[low, high]);
        }
        value
    }

    fn seed_words(&mut self, words: &[u32]) {
        self.state[0] = 19_650_218;
        for index in 1..624 {
            self.state[index] = 1_812_433_253u32
                .wrapping_mul(self.state[index - 1] ^ (self.state[index - 1] >> 30))
                .wrapping_add(index as u32);
        }

        let mut i = 1;
        let mut j = 0;
        let mut remaining = 624.max(words.len());
        while remaining > 0 {
            self.state[i] = (self.state[i]
                ^ (self.state[i - 1] ^ (self.state[i - 1] >> 30)).wrapping_mul(1_664_525))
            .wrapping_add(words[j])
            .wrapping_add(j as u32);
            i += 1;
            j += 1;
            if i >= 624 {
                self.state[0] = self.state[623];
                i = 1;
            }
            if j >= words.len() {
                j = 0;
            }
            remaining -= 1;
        }
        remaining = 623;
        while remaining > 0 {
            self.state[i] = (self.state[i]
                ^ (self.state[i - 1] ^ (self.state[i - 1] >> 30)).wrapping_mul(1_566_083_941))
            .wrapping_sub(i as u32);
            i += 1;
            if i >= 624 {
                self.state[0] = self.state[623];
                i = 1;
            }
            remaining -= 1;
        }
        self.state[0] = 0x8000_0000;
        self.index = 624;
    }

    pub(super) fn seed(&mut self, seed: u32) {
        self.seed_words(&[seed]);
    }

    fn next_u32(&mut self) -> u32 {
        if self.index >= 624 {
            for index in 0..624 {
                let joined = (self.state[index] & 0x8000_0000)
                    | (self.state[(index + 1) % 624] & 0x7fff_ffff);
                let mut value = joined >> 1;
                if joined & 1 != 0 {
                    value ^= 0x9908_b0df;
                }
                self.state[index] = self.state[(index + 397) % 624] ^ value;
            }
            self.index = 0;
        }
        let mut value = self.state[self.index];
        self.index += 1;
        value ^= value >> 11;
        value ^= (value << 7) & 0x9d2c_5680;
        value ^= (value << 15) & 0xefc6_0000;
        value ^= value >> 18;
        value
    }

    fn getrandbits(&mut self, bits: u32) -> u32 {
        if bits == 0 {
            0
        } else {
            self.next_u32() >> (32 - bits)
        }
    }

    pub(super) fn random(&mut self) -> f64 {
        let high = (self.next_u32() >> 5) as u64;
        let low = (self.next_u32() >> 6) as u64;
        ((high << 26) + low) as f64 / 9_007_199_254_740_992.0
    }

    pub fn randbelow(&mut self, upper: usize) -> usize {
        if upper <= 1 {
            return 0;
        }
        let bits = usize::BITS - upper.leading_zeros();
        loop {
            let value = self.getrandbits(bits) as usize;
            if value < upper {
                return value;
            }
        }
    }

    pub(super) fn randint(&mut self, low: i32, high: i32) -> i32 {
        low + self.randbelow((high - low + 1) as usize) as i32
    }

    pub fn uniform(&mut self, low: f64, high: f64) -> f64 {
        low + (high - low) * self.random()
    }
}

#[cfg(test)]
mod tests {
    use super::PythonRandom;

    #[test]
    fn matches_python_random_seeded_values() {
        let mut rng = PythonRandom::new(12_345);
        assert_eq!(rng.random(), 0.416_619_872_545_341_16);
        assert_eq!(rng.randint(0, 63), 1);
        assert_eq!(rng.randint(-30, 30), 22);
    }
}
