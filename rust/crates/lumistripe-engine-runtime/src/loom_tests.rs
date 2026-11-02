use super::QueueMutex;
use super::latest_slot::LatestSlot;
use loom::sync::Arc;
use loom::thread;

#[test]
fn latest_render_slot_stays_bounded_across_producer_consumer_interleavings() {
    loom::model(|| {
        let slot = Arc::new(QueueMutex::new(LatestSlot::default()));
        let producer_slot = Arc::clone(&slot);
        let producer = thread::spawn(move || {
            let mut slot = producer_slot.lock().expect("slot lock poisoned");
            assert!(slot.replace(1_u8).is_none());
            drop(slot);

            thread::yield_now();
            let mut slot = producer_slot.lock().expect("slot lock poisoned");
            let replaced = slot.replace(2_u8);
            assert!(replaced.is_none() || replaced == Some(1));
        });

        let consumer_slot = Arc::clone(&slot);
        let consumer = thread::spawn(move || {
            thread::yield_now();
            consumer_slot.lock().expect("slot lock poisoned").take()
        });

        let consumed = consumer.join().expect("consumer thread panicked");
        producer.join().expect("producer thread panicked");
        let mut slot = slot.lock().expect("slot lock poisoned");
        let remaining = slot.take();
        assert!(slot.is_none());

        match consumed {
            Some(1) => assert_eq!(remaining, Some(2)),
            Some(2) => assert_eq!(remaining, None),
            None => assert_eq!(remaining, Some(2)),
            Some(value) => panic!("unexpected render sequence {value}"),
        }
    });
}
