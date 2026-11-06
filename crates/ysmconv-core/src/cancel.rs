use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

type Callback = Arc<dyn Fn() + Send + Sync>;

/// 取消令牌: 线程间共享, 取消时依次执行注册的回调(终止内核进程)。
#[derive(Clone, Default)]
pub struct CancelToken {
    inner: Arc<Inner>,
}

#[derive(Default)]
struct Inner {
    cancelled: AtomicBool,
    next_id: AtomicU64,
    callbacks: Mutex<Vec<(u64, Callback)>>,
}

impl CancelToken {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn is_cancelled(&self) -> bool {
        self.inner.cancelled.load(Ordering::SeqCst)
    }

    /// 取消; 重复调用无副作用。回调在调用线程上执行。
    pub fn cancel(&self) {
        if self.inner.cancelled.swap(true, Ordering::SeqCst) {
            return;
        }
        let callbacks: Vec<Callback> = lock(&self.inner.callbacks).drain(..).map(|(_, c)| c).collect();
        for callback in callbacks {
            callback();
        }
    }

    /// 注册取消回调; 已经取消时当场执行。返回的守卫析构时注销回调。
    pub fn register(&self, callback: impl Fn() + Send + Sync + 'static) -> Registration {
        let callback: Callback = Arc::new(callback);
        let id = self.inner.next_id.fetch_add(1, Ordering::Relaxed);
        {
            // 在锁里判断"已取消": cancel 先置位再取锁清空列表, 两边不会漏掉这个回调
            let mut list = lock(&self.inner.callbacks);
            if !self.is_cancelled() {
                list.push((id, callback));
                return Registration {
                    token: Some(self.clone()),
                    id,
                };
            }
        }
        callback();
        Registration { token: None, id }
    }

    /// 子令牌: 父令牌取消时跟着取消, 子令牌自己取消不影响父令牌。守卫存活期间保持联动。
    pub fn child(&self) -> (CancelToken, Registration) {
        let child = CancelToken::new();
        let linked = child.clone();
        let registration = self.register(move || linked.cancel());
        (child, registration)
    }
}

/// [`CancelToken::register`] 的守卫。
pub struct Registration {
    token: Option<CancelToken>,
    id: u64,
}

impl Drop for Registration {
    fn drop(&mut self) {
        if let Some(token) = self.token.take() {
            lock(&token.inner.callbacks).retain(|(id, _)| *id != self.id);
        }
    }
}

fn lock<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::AtomicUsize;

    #[test]
    fn callbacks_run_once_and_late_registration_runs_immediately() {
        let token = CancelToken::new();
        let hits = Arc::new(AtomicUsize::new(0));
        let h = hits.clone();
        let _reg = token.register(move || {
            h.fetch_add(1, Ordering::SeqCst);
        });
        token.cancel();
        token.cancel();
        assert_eq!(hits.load(Ordering::SeqCst), 1);
        let h = hits.clone();
        let _late = token.register(move || {
            h.fetch_add(10, Ordering::SeqCst);
        });
        assert_eq!(hits.load(Ordering::SeqCst), 11);
    }

    #[test]
    fn dropped_registration_does_not_fire() {
        let token = CancelToken::new();
        let hits = Arc::new(AtomicUsize::new(0));
        let h = hits.clone();
        drop(token.register(move || {
            h.fetch_add(1, Ordering::SeqCst);
        }));
        token.cancel();
        assert_eq!(hits.load(Ordering::SeqCst), 0);
    }

    #[test]
    fn child_follows_parent_but_not_back() {
        let parent = CancelToken::new();
        let (child, _reg) = parent.child();
        child.cancel();
        assert!(!parent.is_cancelled());
        let (child2, _reg2) = parent.child();
        parent.cancel();
        assert!(child2.is_cancelled());
    }
}
