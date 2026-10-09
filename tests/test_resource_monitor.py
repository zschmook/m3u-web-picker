import unittest
from unittest.mock import patch
import resource_monitor as monitor


class ResourceMonitorTests(unittest.TestCase):
    def setUp(self):
        self.previous = monitor._previous
        monitor._previous = None
        self.gpu = patch.object(monitor, '_gpu_pending', True)
        self.gpu.start()
        self.addCleanup(self.gpu.stop)
        self.addCleanup(setattr, monitor, '_previous', self.previous)

    def test_cpu_uses_delta_and_unavailable_is_not_zero(self):
        with patch.object(monitor, 'counters', side_effect=[
            (10, 100, 'Container', 25, 100, 'Container'),
            (12, 110, 'Container', 30, 100, 'Container'),
            OSError('Counters unavailable')]):
            first = monitor.snapshot()
            second = monitor.snapshot()
            missing = monitor.snapshot()
        self.assertIsNone(first['cpu_percent'])
        self.assertEqual(first['ram_percent'], 25)
        self.assertEqual(second['cpu_percent'], 20)
        self.assertEqual(second['ram_percent'], 30)
        self.assertIsNone(missing['cpu_percent'])
        self.assertIsNone(missing['ram_percent'])

    def test_container_quota_and_memory_cache_are_accounted_for(self):
        files = {
            '/proc/meminfo': 'MemTotal: 1000 kB\nMemAvailable: 600 kB',
            '/sys/fs/cgroup/memory.current': '400000',
            '/sys/fs/cgroup/memory.max': '500000',
            '/sys/fs/cgroup/memory.stat': 'inactive_file 100000',
            '/sys/fs/cgroup/cpu.stat': 'usage_usec 5000000',
            '/sys/fs/cgroup/cpu.max': '100000 100000',
        }
        with patch.object(monitor, 'read', side_effect=files.__getitem__), patch.object(monitor.time, 'monotonic', return_value=10):
            busy, total, scope, used, capacity, ram_scope = monitor.counters()
        self.assertEqual((busy, total), (5, 10))
        self.assertEqual((used, capacity), (300000, 500000))
        self.assertEqual(monitor.percent(used, capacity), 60)

    def test_gpu_uses_busiest_exposed_device_and_timeout_is_unknown(self):
        with patch.object(monitor.shutil, 'which', return_value='/usr/bin/nvidia-smi'), patch.object(monitor.subprocess, 'run') as run:
            run.return_value.stdout = '20\n75\n'
            self.assertEqual(monitor.read_gpu(), 75)
            self.assertLessEqual(run.call_args.kwargs['timeout'], 1)
            run.side_effect = monitor.subprocess.TimeoutExpired('nvidia-smi', 0.75)
            with patch.object(monitor.Path, 'glob', return_value=[]):
                self.assertIsNone(monitor.read_gpu())


if __name__ == '__main__':
    unittest.main()
