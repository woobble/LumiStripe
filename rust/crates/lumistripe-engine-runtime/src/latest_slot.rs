//! Single-item latest-wins storage shared by scheduled and direct render work.

#[derive(Debug)]
pub(crate) struct LatestSlot<T> {
    value: Option<T>,
}

impl<T> Default for LatestSlot<T> {
    fn default() -> Self {
        Self { value: None }
    }
}

impl<T> LatestSlot<T> {
    pub(crate) fn as_ref(&self) -> Option<&T> {
        self.value.as_ref()
    }

    pub(crate) fn as_mut(&mut self) -> Option<&mut T> {
        self.value.as_mut()
    }

    pub(crate) fn clear(&mut self) {
        self.value = None;
    }

    pub(crate) fn is_none(&self) -> bool {
        self.value.is_none()
    }

    pub(crate) fn is_some(&self) -> bool {
        self.value.is_some()
    }

    pub(crate) fn replace(&mut self, value: T) -> Option<T> {
        self.value.replace(value)
    }

    pub(crate) fn take(&mut self) -> Option<T> {
        self.value.take()
    }
}
