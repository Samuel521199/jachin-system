"use client";

import { useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";

interface ToastProps {
  message: string;
  visible: boolean;
  onClose: () => void;
  duration?: number;
}

export default function Toast({ message, visible, onClose, duration = 3000 }: ToastProps) {
  useEffect(() => {
    if (!visible) return;
    const t = setTimeout(onClose, duration);
    return () => clearTimeout(t);
  }, [visible, duration, onClose]);

  return (
    <AnimatePresence>
      {visible && (
        <motion.div
          initial={{ opacity: 0, y: 20, scale: 0.95 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: -10, scale: 0.95 }}
          transition={{ duration: 0.2 }}
          className="fixed bottom-8 left-1/2 -translate-x-1/2 z-[100] px-6 py-4 rounded-xl bg-green-500/20 border border-green-500/50 text-green-400 backdrop-blur-xl shadow-[0_0_30px_rgba(34,197,94,0.2)]"
        >
          {message}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
