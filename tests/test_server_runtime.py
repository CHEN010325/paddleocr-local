import asyncio
import importlib
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import HTTPException


def response(status=200, *, payload=None, text=None):
    request = httpx.Request("GET", "http://docker/test")
    if payload is not None:
        return httpx.Response(status, json=payload, request=request)
    return httpx.Response(status, text=text or "", request=request)


class FakeAsyncClient:
    def __init__(self, *, request_response=None, get_response=None, post_response=None):
        self.request_response = request_response if request_response is not None else response()
        self.get_response = get_response if get_response is not None else response()
        self.post_response = post_response if post_response is not None else response()
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def request(self, method, path, **kwargs):
        self.calls.append(("request", method, path, kwargs))
        return self.request_response

    async def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        if isinstance(self.get_response, Exception):
            raise self.get_response
        return self.get_response

    async def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        return self.post_response


class FakeTask:
    def __init__(self, done=False):
        self._done = done
        self.cancelled = False

    def done(self):
        return self._done

    def cancel(self):
        self.cancelled = True


class ServerDockerRuntimeTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = importlib.import_module("server")

    async def asyncSetUp(self):
        self.server.model_runtime_lock = asyncio.Lock()
        self.server.model_runtime_task = None
        self.server.unlimited_ocr_backend_task = None
        self.server.ocr_active_count = 0
        self.server.controller_ocr_leases.clear()
        self.server.set_model_runtime_operation("idle", "", self.server.DEFAULT_RUNTIME_MODEL_ID)

    async def asyncTearDown(self):
        self.server.controller_ocr_leases.clear()

    async def test_docker_api_request_builds_uds_client(self):
        client = FakeAsyncClient(request_response=response(payload={"ok": True}))
        with (
            patch.object(self.server.httpx, "AsyncHTTPTransport", return_value="transport") as transport,
            patch.object(self.server.httpx, "AsyncClient", return_value=client) as client_class,
        ):
            result = await self.server.docker_api_request("POST", "/action", json={"x": 1})

        self.assertEqual(result.json(), {"ok": True})
        transport.assert_called_once_with(uds=self.server.DOCKER_SOCKET_PATH)
        self.assertEqual(client_class.call_args.kwargs["base_url"], "http://docker")
        self.assertEqual(client.calls[0][1:3], ("POST", "/action"))

    async def test_controller_api_request_requires_remote_configuration(self):
        with (
            patch.object(self.server, "MODEL_CONTROL_MODE", "docker"),
            patch.object(self.server, "MODEL_CONTROLLER_URL", ""),
        ):
            with self.assertRaises(HTTPException) as raised:
                await self.server.controller_api_request("GET", "/model-runtime")

        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("not configured", raised.exception.detail)

    async def test_controller_api_request_adds_token_and_maps_transport_failure(self):
        client = FakeAsyncClient(request_response=response(payload={"ok": True}))
        with (
            patch.object(self.server, "MODEL_CONTROL_MODE", "remote"),
            patch.object(self.server, "MODEL_CONTROLLER_URL", "http://controller:8001"),
            patch.object(self.server, "MODEL_CONTROLLER_TOKEN", "controller-secret"),
            patch.object(self.server.httpx, "AsyncClient", return_value=client),
        ):
            result = await self.server.controller_api_request(
                "POST",
                "/model-runtime/switch",
                headers={"X-Trace": "test"},
                json={"modelId": "pp-ocrv6"},
            )

        self.assertEqual(result, {"ok": True})
        request_kwargs = client.calls[0][3]
        self.assertEqual(request_kwargs["headers"]["X-Trace"], "test")
        self.assertEqual(
            request_kwargs["headers"]["X-Pandocr-Controller-Token"],
            "controller-secret",
        )

        unavailable_client = FakeAsyncClient()
        unavailable_client.request = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        with (
            patch.object(self.server, "MODEL_CONTROL_MODE", "remote"),
            patch.object(self.server, "MODEL_CONTROLLER_URL", "http://controller:8001"),
            patch.object(self.server.httpx, "AsyncClient", return_value=unavailable_client),
        ):
            with self.assertRaises(HTTPException) as raised:
                await self.server.controller_api_request("GET", "/model-runtime")

        self.assertEqual(raised.exception.status_code, 503)
        self.assertIn("connection refused", raised.exception.detail)

    async def test_controller_api_request_preserves_errors_and_rejects_non_object_payload(self):
        cases = (
            (response(409, payload={"detail": "OCR lease active"}), 409, "OCR lease active"),
            (response(502, text="plain controller failure"), 502, "plain controller failure"),
            (response(500, payload={"detail": ""}), 500, "Model controller request failed"),
        )
        for controller_response, expected_status, expected_detail in cases:
            with self.subTest(status=expected_status, detail=expected_detail):
                client = FakeAsyncClient(request_response=controller_response)
                with (
                    patch.object(self.server, "MODEL_CONTROL_MODE", "remote"),
                    patch.object(self.server, "MODEL_CONTROLLER_URL", "http://controller:8001"),
                    patch.object(self.server.httpx, "AsyncClient", return_value=client),
                ):
                    with self.assertRaises(HTTPException) as raised:
                        await self.server.controller_api_request("GET", "/model-runtime")
                self.assertEqual(raised.exception.status_code, expected_status)
                self.assertEqual(raised.exception.detail, expected_detail)

        client = FakeAsyncClient(request_response=response(payload=["not", "an", "object"]))
        with (
            patch.object(self.server, "MODEL_CONTROL_MODE", "remote"),
            patch.object(self.server, "MODEL_CONTROLLER_URL", "http://controller:8001"),
            patch.object(self.server.httpx, "AsyncClient", return_value=client),
        ):
            with self.assertRaises(HTTPException) as raised:
                await self.server.controller_api_request("GET", "/model-runtime")
        self.assertEqual(raised.exception.status_code, 502)
        self.assertIn("Invalid model controller response", raised.exception.detail)

    async def test_docker_log_decoding_and_container_log_statuses(self):
        def frame(stream, payload):
            return bytes([stream, 0, 0, 0]) + len(payload).to_bytes(4, "big") + payload

        multiplexed = frame(1, b"stdout\n") + frame(2, b"stderr\x00\n")
        self.assertEqual(
            self.server.decode_docker_log_stream(multiplexed),
            "stdout\nstderr",
        )
        truncated = bytes([1, 0, 0, 0]) + (20).to_bytes(4, "big") + b"partial"
        self.assertTrue(self.server.decode_docker_log_stream(truncated).endswith("partial"))
        self.assertEqual(self.server.decode_docker_log_stream(b"plain\x00 log\n"), "plain log")

        ok_response = httpx.Response(
            200,
            content=multiplexed,
            request=httpx.Request("GET", "http://docker/logs"),
        )
        api = AsyncMock(side_effect=[response(404), ok_response, response(500, text="boom")])
        with patch.object(self.server, "docker_api_request", new=api):
            self.assertEqual(await self.server.docker_container_logs("missing"), "")
            self.assertEqual(
                await self.server.docker_container_logs("worker/name", tail=0, timestamps=False),
                "stdout\nstderr",
            )
            with self.assertRaises(httpx.HTTPStatusError):
                await self.server.docker_container_logs("broken")

        request_path = api.await_args_list[1].args[1]
        self.assertIn("worker%2Fname", request_path)
        self.assertIn("tail=1&timestamps=0", request_path)

    async def test_gpu_probe_image_prefers_requested_model_and_handles_no_image(self):
        config = {
            "fallback": {"containers": ["fallback-api"]},
            "target": {"containers": ["target-server", "target-api"]},
        }
        exists = AsyncMock(side_effect=[False, True])
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(
                self.server,
                "docker_image_name_for",
                side_effect=lambda service: f"image:{service}",
            ),
            patch.object(self.server, "docker_image_exists", new=exists),
        ):
            selected = await self.server.gpu_probe_image("target")

        self.assertEqual(selected, "image:target-api")
        self.assertEqual(
            [call.args[0] for call in exists.await_args_list],
            ["image:target-server", "image:target-api"],
        )

        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(
                self.server,
                "docker_image_name_for",
                side_effect=lambda service: f"image:{service}",
            ),
            patch.object(self.server, "docker_image_exists", new=AsyncMock(return_value=False)),
        ):
            self.assertIsNone(await self.server.gpu_probe_image("unknown"))

    async def test_issue_7_8188_mib_gpu_rejects_paddle_vl_and_recommends_ppocr(self):
        self.assertEqual(
            self.server.MODEL_MINIMUM_FREE_MIB,
            {
                "paddleocr-vl-1.6": 6656,
                "pp-ocrv6": 4096,
                "unlimited-ocr": 6656,
                "ovisocr2": 6656,
                "hpd-parsing": 6656,
            },
        )
        result = self.server.gpu_compatibility(
            [{"name": "RTX 4070 Laptop GPU", "totalMiB": 8188, "freeMiB": 7800}]
        )
        self.assertIn("pp-ocrv6", result["runnableModelIds"])
        self.assertNotIn("paddleocr-vl-1.6", result["runnableModelIds"])
        self.assertEqual(result["models"]["paddleocr-vl-1.6"]["level"], "unsupported")
        self.assertIn(
            "PANDOCR_VLLM_MIN_REQUIRED_MIB=6656",
            result["models"]["paddleocr-vl-1.6"]["lowMemoryEnv"],
        )
        launcher = (Path(__file__).resolve().parents[1] / "start-vlm.sh").read_text(encoding="utf-8")
        self.assertIn('PANDOCR_VLLM_MIN_TOTAL_MIB:-11264', launcher)
        self.assertIn('PANDOCR_VLLM_MIN_REQUIRED_MIB:-6656', launcher)

    async def test_gpu_probe_parses_nvidia_smi_and_removes_probe_container(self):
        api = AsyncMock(
            side_effect=[
                response(201, payload={"Id": "probe"}),
                response(204),
                response(200, payload={"StatusCode": 0}),
                response(204),
            ]
        )
        with (
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(self.server, "gpu_probe_image", new=AsyncMock(return_value="gpu:image")),
            patch.object(self.server, "docker_api_request", new=api),
            patch.object(
                self.server,
                "docker_container_logs",
                new=AsyncMock(return_value="0, NVIDIA GeForce RTX 3060, 12288, 11000"),
            ),
        ):
            result = await self.server.probe_gpu_preflight("paddleocr-vl-1.6", refresh=True)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["gpus"][0]["totalMiB"], 12288)
        self.assertTrue(result["models"]["paddleocr-vl-1.6"]["supported"])
        self.assertEqual(result["models"]["paddleocr-vl-1.6"]["minimumFreeMiB"], 6656)
        self.assertEqual(api.await_args_list[-1].args[0], "DELETE")

    async def test_gpu_probe_reports_unavailable_control_without_selecting_image(self):
        image = AsyncMock()
        with (
            patch.object(self.server, "gpu_preflight_cache", {"updated_at": 0.0, "data": None}),
            patch.object(self.server, "model_control_available", return_value=False),
            patch.object(self.server, "gpu_probe_image", new=image),
        ):
            result = await self.server.probe_gpu_preflight(refresh=True)

        self.assertEqual(result["status"], "unavailable")
        self.assertIn("Docker model control", result["reason"])
        image.assert_not_awaited()

    async def test_gpu_probe_reports_create_and_start_failures_and_still_cleans_up(self):
        cases = (
            (
                [response(500, text="create denied"), response(204)],
                "create failed: create denied",
            ),
            (
                [response(201), response(500, text="start denied"), response(204)],
                "start failed: start denied",
            ),
        )
        for api_responses, expected_reason in cases:
            with self.subTest(reason=expected_reason):
                api = AsyncMock(side_effect=api_responses)
                with (
                    patch.object(self.server, "gpu_preflight_cache", {"updated_at": 0.0, "data": None}),
                    patch.object(self.server, "model_control_available", return_value=True),
                    patch.object(self.server, "gpu_probe_image", new=AsyncMock(return_value="gpu:image")),
                    patch.object(self.server, "docker_api_request", new=api),
                    patch.object(self.server, "docker_container_logs", new=AsyncMock()) as logs,
                ):
                    result = await self.server.probe_gpu_preflight(refresh=True)

                self.assertEqual(result["status"], "unavailable")
                self.assertIn(expected_reason, result["reason"])
                self.assertEqual(api.await_args_list[-1].args[0], "DELETE")
                logs.assert_not_awaited()

    async def test_gpu_probe_reports_process_and_parse_failures_and_still_cleans_up(self):
        cases = (
            (3, "", "nvidia-smi exited with code 3"),
            (0, "junk\n0, GPU, not-a-number, invalid\nx, GPU, 100, 90", "Could not parse"),
        )
        for exit_code, logs, expected_reason in cases:
            with self.subTest(exit_code=exit_code, reason=expected_reason):
                api = AsyncMock(
                    side_effect=[
                        response(201),
                        response(204),
                        response(200, payload={"StatusCode": exit_code}),
                        response(204),
                    ]
                )
                with (
                    patch.object(self.server, "gpu_preflight_cache", {"updated_at": 0.0, "data": None}),
                    patch.object(self.server, "model_control_available", return_value=True),
                    patch.object(self.server, "gpu_probe_image", new=AsyncMock(return_value="gpu:image")),
                    patch.object(self.server, "docker_api_request", new=api),
                    patch.object(self.server, "docker_container_logs", new=AsyncMock(return_value=logs)),
                ):
                    result = await self.server.probe_gpu_preflight(refresh=True)

                self.assertEqual(result["status"], "unavailable")
                self.assertIn(expected_reason, result["reason"])
                self.assertEqual(api.await_args_list[-1].args[0], "DELETE")

    async def test_gpu_probe_caches_missing_image_and_external_runtime_skips_probe(self):
        self.server.gpu_preflight_cache = {"updated_at": 0.0, "data": None}
        image = AsyncMock(return_value=None)
        with (
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(self.server, "gpu_probe_image", new=image),
        ):
            first = await self.server.probe_gpu_preflight("paddleocr-vl-1.6")
            second = await self.server.probe_gpu_preflight("paddleocr-vl-1.6")
        self.assertEqual(first["status"], "unavailable")
        self.assertIs(first, second)
        image.assert_awaited_once()

        with (
            patch.object(self.server, "model_control_available", return_value=False),
            patch.object(self.server, "model_runtime_status", new=AsyncMock(return_value={"ready": False, "running": False})),
            patch.object(self.server, "probe_gpu_preflight", new=AsyncMock()) as probe,
        ):
            payload = await self.server.build_model_runtime_payload()
        self.assertIsNone(payload["gpuPreflight"])
        probe.assert_not_awaited()

    async def test_gpu_preflight_rejects_too_small_gpu_before_start(self):
        preflight = {
            "status": "ready",
            "gpus": [{"name": "small GPU", "totalMiB": 4096}],
            "runnableModelIds": ["pp-ocrv6"],
            "models": {
                "hpd-parsing": {
                    "supported": False,
                    "minimumMiB": 8192,
                }
            },
        }
        with patch.object(
            self.server,
            "probe_gpu_preflight",
            new=AsyncMock(return_value=preflight),
        ):
            with self.assertRaisesRegex(RuntimeError, "Runnable models: pp-ocrv6"):
                await self.server.ensure_model_gpu_compatible("hpd-parsing")

    async def test_gpu_compatibility_gate_reports_probe_failure_and_returns_supported_probe(self):
        with patch.object(
            self.server,
            "probe_gpu_preflight",
            new=AsyncMock(return_value={"status": "unavailable", "reason": "toolkit missing"}),
        ):
            with self.assertRaisesRegex(RuntimeError, "toolkit missing"):
                await self.server.ensure_model_gpu_compatible("pp-ocrv6")

        supported = {
            "status": "ready",
            "models": {"pp-ocrv6": {"supported": True}},
            "gpus": [{"name": "GPU", "totalMiB": 8192}],
        }
        with patch.object(
            self.server,
            "probe_gpu_preflight",
            new=AsyncMock(return_value=supported),
        ):
            self.assertIs(await self.server.ensure_model_gpu_compatible("pp-ocrv6"), supported)

    async def test_gpu_release_gate_requires_consecutive_stable_free_memory_samples(self):
        samples = [7000, 7300, 7220, 7250]

        async def probe(_model_id, *, refresh=False):
            self.assertTrue(refresh)
            free_mib = samples.pop(0)
            return {
                "status": "ready",
                "gpus": [
                    {
                        "index": 0,
                        "deviceId": "0",
                        "name": "test GPU",
                        "totalMiB": 16384,
                        "freeMiB": free_mib,
                    }
                ],
            }

        config = {
            "target": {
                "gpu_memory": {
                    "minimum_mib": 7680,
                    "minimum_free_mib": 6656,
                }
            }
        }
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(self.server, "probe_gpu_preflight", new=AsyncMock(side_effect=probe)),
            patch.object(self.server, "GPU_RELEASE_STABLE_SAMPLES", 3),
            patch.object(self.server, "GPU_RELEASE_TOLERANCE_MIB", 128),
            patch.object(self.server, "GPU_RELEASE_SAMPLE_INTERVAL_SECONDS", 0),
        ):
            result = await self.server.wait_for_stable_gpu_release("target", timeout=10)

        self.assertEqual(result["stableSamples"], [7300, 7220, 7250])
        self.assertEqual(result["observedSamples"], [7000, 7300, 7220, 7250])
        self.assertEqual(result["minimumFreeMiB"], 6656)

    async def test_gpu_release_gate_fails_closed_on_probe_or_free_memory_failure(self):
        config = {
            "target": {
                "gpu_memory": {
                    "minimum_mib": 7680,
                    "minimum_free_mib": 6656,
                }
            }
        }
        unavailable = {"status": "unavailable", "reason": "nvidia-smi denied"}
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(
                self.server,
                "probe_gpu_preflight",
                new=AsyncMock(return_value=unavailable),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "failed closed.*nvidia-smi denied"):
                await self.server.wait_for_stable_gpu_release("target", timeout=10)

        malformed = {
            "status": "ready",
            "gpus": [{"index": 0, "deviceId": "0", "freeMiB": None}],
        }
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(
                self.server,
                "probe_gpu_preflight",
                new=AsyncMock(return_value=malformed),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "freeMiB is missing or invalid"):
                await self.server.wait_for_stable_gpu_release("target", timeout=10)

        low = {
            "status": "ready",
            "gpus": [{"index": 0, "deviceId": "0", "freeMiB": 6000}],
        }
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(
                self.server,
                "probe_gpu_preflight",
                new=AsyncMock(return_value=low),
            ),
            patch.object(self.server, "GPU_RELEASE_TIMEOUT", 0.001),
            patch.object(self.server, "GPU_RELEASE_SAMPLE_INTERVAL_SECONDS", 0.001),
        ):
            with self.assertRaisesRegex(TimeoutError, "needs 6656 MiB free"):
                await self.server.wait_for_stable_gpu_release("target", timeout=10)

    async def test_gpu_release_gate_selects_configured_gpu_and_never_guesses(self):
        preflight = {
            "gpus": [
                {"index": 0, "deviceId": "0", "freeMiB": 12000},
                {"index": 1, "deviceId": "1", "freeMiB": 1000},
            ]
        }
        with patch.object(self.server, "PANDOCR_GPU_DEVICE_ID", "1"):
            selected = self.server.selected_gpu_from_preflight(preflight)
        self.assertEqual(selected["index"], 1)

        with patch.object(self.server, "PANDOCR_GPU_DEVICE_ID", "2"):
            with self.assertRaisesRegex(RuntimeError, "did not return selected device 2"):
                self.server.selected_gpu_from_preflight(preflight)

    async def test_selected_gpu_rejects_missing_data_and_supports_index_or_single_gpu(self):
        for invalid in ({}, {"gpus": None}, {"gpus": "not-a-list"}):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(RuntimeError, "could not identify"):
                    self.server.selected_gpu_from_preflight(invalid)

        with patch.object(self.server, "PANDOCR_GPU_DEVICE_ID", "7"):
            selected = self.server.selected_gpu_from_preflight(
                {"gpus": [None, {"index": 7, "deviceId": "other", "freeMiB": 7000}]}
            )
        self.assertEqual(selected["index"], 7)

        only_gpu = {"index": 0, "deviceId": "remapped", "freeMiB": 7000}
        with patch.object(self.server, "PANDOCR_GPU_DEVICE_ID", "physical-gpu-uuid"):
            self.assertIs(
                self.server.selected_gpu_from_preflight({"gpus": [only_gpu]}),
                only_gpu,
            )

    async def test_gpu_release_gate_rejects_missing_threshold_and_nonpositive_timeout(self):
        with patch.dict(
            self.server.MODEL_RUNTIME_CONFIG,
            {"target": {"gpu_memory": {}}},
            clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "no minimum_free_mib"):
                await self.server.wait_for_stable_gpu_release("target", timeout=10)

        config = {"target": {"gpu_memory": {"minimum_free_mib": 6656}}}
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(self.server, "GPU_RELEASE_TIMEOUT", 0),
        ):
            with self.assertRaisesRegex(RuntimeError, "no remaining timeout"):
                await self.server.wait_for_stable_gpu_release("target", timeout=10)

    async def test_gpu_release_gate_wraps_probe_timeout_and_breaks_after_expired_sample(self):
        config = {"target": {"gpu_memory": {"minimum_free_mib": 6656}}}
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(
                self.server,
                "probe_gpu_preflight",
                new=AsyncMock(side_effect=TimeoutError("probe deadline")),
            ),
        ):
            with self.assertRaisesRegex(TimeoutError, "timed out while probing selected GPU"):
                await self.server.wait_for_stable_gpu_release("target", timeout=10)

        clock = MagicMock(side_effect=[0.0, 0.0, 2.0])
        low_sample = {
            "status": "ready",
            "gpus": [{"index": 0, "deviceId": "0", "freeMiB": 7000}],
        }
        probe = AsyncMock(return_value=low_sample)
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(self.server, "time", SimpleNamespace(monotonic=clock)),
            patch.object(self.server, "probe_gpu_preflight", new=probe),
            patch.object(self.server, "GPU_RELEASE_TIMEOUT", 1),
            patch.object(self.server, "MODEL_SWITCH_TIMEOUT", 10),
            patch.object(self.server, "GPU_RELEASE_STABLE_SAMPLES", 2),
        ):
            with self.assertRaisesRegex(TimeoutError, "last free=7000 MiB"):
                await self.server.wait_for_stable_gpu_release("target", timeout=10)
        probe.assert_awaited_once()

    async def test_failure_diagnostics_returns_docker_log_commands_and_tail(self):
        config = {"broken": {"containers": ["model-server", "model-api"]}}
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(
                self.server,
                "docker_container_logs",
                new=AsyncMock(side_effect=["CUDA out of memory", "adapter waiting"]),
            ),
        ):
            diagnostics = await self.server.model_failure_diagnostics(
                "broken", RuntimeError("startup failed")
            )
        self.assertEqual(
            diagnostics["logCommands"],
            [
                "docker logs --tail 200 model-server",
                "docker logs --tail 200 model-api",
            ],
        )
        self.assertIn("CUDA out of memory", diagnostics["logs"][0]["tail"])

    async def test_inspect_container_handles_unavailable_missing_and_running(self):
        with patch.object(self.server, "model_control_available", return_value=False):
            unavailable = await self.server.inspect_container("worker")
        self.assertEqual(unavailable["state"], "unknown")

        with (
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(self.server, "docker_api_request", new=AsyncMock(return_value=response(404))),
        ):
            missing = await self.server.inspect_container("worker")
        self.assertEqual(missing["state"], "missing")

        payload = {
            "State": {
                "Running": True,
                "Status": "running",
                "Health": {"Status": "healthy"},
            }
        }
        with (
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(
                self.server,
                "docker_api_request",
                new=AsyncMock(return_value=response(payload=payload)),
            ),
        ):
            running = await self.server.inspect_container("worker")
        self.assertTrue(running["exists"])
        self.assertTrue(running["running"])
        self.assertEqual(running["health"], "healthy")

    async def test_container_actions_cover_success_validation_and_failure(self):
        with patch.object(self.server, "model_control_available", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "not available"):
                await self.server.docker_container_action("worker", "start")

        for action, status in (("stop", 404), ("start", 304)):
            with (
                patch.object(self.server, "model_control_available", return_value=True),
                patch.object(
                    self.server,
                    "docker_api_request",
                    new=AsyncMock(return_value=response(status)),
                ),
            ):
                await self.server.docker_container_action("worker", action)

        with patch.object(self.server, "model_control_available", return_value=True):
            with self.assertRaises(ValueError):
                await self.server.docker_container_action("worker", "restart")

        with (
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(
                self.server,
                "docker_api_request",
                new=AsyncMock(return_value=response(500, text="failed")),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "Docker start failed"):
                await self.server.docker_container_action("worker", "start")

    async def test_image_names_refs_and_existence(self):
        expected_services = {
            "paddleocr-vlm-server",
            "paddleocr-vl-api",
            "paddleocr-ocr-api",
            "unlimited-ocr-api",
            "unlimited-ocr-sglang",
            "ovisocr2-api",
            "hpd-parsing-server",
            "hpd-parsing-api",
        }
        for service in expected_services:
            self.assertTrue(self.server.docker_image_name_for(service))
        with self.assertRaises(ValueError):
            self.server.docker_image_name_for("unknown")

        self.assertEqual(
            self.server.split_docker_image_ref("registry:5000/repo/image:tag"),
            ("registry:5000/repo/image", "tag"),
        )
        self.assertEqual(
            self.server.split_docker_image_ref("registry:5000/repo/image"),
            ("registry:5000/repo/image", "latest"),
        )
        digest_ref = "registry/repo/image:tag@sha256:abc"
        self.assertEqual(self.server.split_docker_image_ref(digest_ref), (digest_ref, ""))

        with patch.object(self.server, "model_control_available", return_value=False):
            self.assertFalse(await self.server.docker_image_exists("image"))
        with (
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(self.server, "docker_api_request", new=AsyncMock(return_value=response(404))),
        ):
            self.assertFalse(await self.server.docker_image_exists("image"))
        with (
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(
                self.server,
                "docker_api_request",
                new=AsyncMock(return_value=response(payload={"Id": "sha"})),
            ),
        ):
            self.assertTrue(await self.server.docker_image_exists("image"))

    async def test_pull_and_build_images_cover_all_outcomes(self):
        with (
            patch.object(self.server, "docker_image_exists", new=AsyncMock(return_value=True)),
            patch.object(self.server, "docker_api_request", new=AsyncMock()) as request_mock,
        ):
            await self.server.docker_pull_image("repo/image:tag")
            request_mock.assert_not_awaited()

        with (
            patch.object(self.server, "docker_image_exists", new=AsyncMock(return_value=False)),
            patch.object(
                self.server,
                "docker_api_request",
                new=AsyncMock(return_value=response(500, text="pull failed")),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "Docker pull failed"):
                await self.server.docker_pull_image("repo/image:tag")

        with (
            patch.object(self.server, "docker_image_exists", new=AsyncMock(return_value=False)),
            patch.object(
                self.server,
                "docker_api_request",
                new=AsyncMock(return_value=response(200, text="ok")),
            ) as request_mock,
        ):
            await self.server.docker_pull_image("repo/image")
        self.assertIn("fromImage=repo%2Fimage", request_mock.await_args.args[1])

        with (
            patch.object(self.server, "docker_image_exists", new=AsyncMock(return_value=False)),
            patch.object(
                self.server,
                "docker_api_request",
                new=AsyncMock(return_value=response(200, text="ok")),
            ) as request_mock,
        ):
            await self.server.docker_pull_image("repo/image:tag@sha256:abc")
        self.assertNotIn("&tag=", request_mock.await_args.args[1])

        with (
            patch.object(self.server, "docker_image_exists", new=AsyncMock(return_value=True)),
            patch.object(self.server, "docker_api_request", new=AsyncMock()) as request_mock,
        ):
            await self.server.docker_build_image("paddleocr-ocr-api")
            request_mock.assert_not_awaited()

        for build_response, message in (
            (response(500, text="bad build"), "Docker build failed"),
            (
                response(
                    200,
                    text='not-json\n{"stream":"step"}\n{"error":"layer failed"}',
                ),
                "layer failed",
            ),
        ):
            with (
                patch.object(self.server, "docker_image_exists", new=AsyncMock(return_value=False)),
                patch.object(
                    self.server,
                    "make_docker_build_context",
                    return_value=b"context",
                ),
                patch.object(
                    self.server,
                    "docker_api_request",
                    new=AsyncMock(return_value=build_response),
                ),
            ):
                with self.assertRaisesRegex(RuntimeError, message):
                    await self.server.docker_build_image("paddleocr-ocr-api")

        with (
            patch.object(self.server, "docker_image_exists", new=AsyncMock(return_value=False)),
            patch.object(self.server, "make_docker_build_context", return_value=b"context"),
            patch.object(
                self.server,
                "docker_api_request",
                new=AsyncMock(return_value=response(200, text='invalid\n{"stream":"done"}')),
            ),
        ):
            await self.server.docker_build_image("unlimited-ocr-sglang")

    async def test_dockerfile_resolution_and_build_arguments(self):
        self.assertTrue(self.server.dockerfile_path_for("paddleocr-ocr-api").is_file())
        self.assertEqual(self.server.docker_build_args_for("unknown"), {})
        with self.assertRaises(ValueError):
            self.server.dockerfile_path_for("unknown")
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(self.server, "PROJECT_ROOT", Path(directory)):
                with self.assertRaisesRegex(RuntimeError, "Missing Dockerfile.ocr"):
                    self.server.dockerfile_path_for("paddleocr-ocr-api")

    async def test_self_inspection_network_and_host_root_fallbacks(self):
        with patch.object(
            self.server,
            "docker_api_request",
            new=AsyncMock(return_value=response(404)),
        ):
            self.assertEqual(await self.server.docker_inspect_self(), {})
        with patch.object(
            self.server,
            "docker_api_request",
            new=AsyncMock(return_value=response(payload=["not", "dict"])),
        ):
            self.assertEqual(await self.server.docker_inspect_self(), {})

        with patch.object(self.server, "docker_inspect_self", new=AsyncMock(return_value={})):
            self.assertEqual(
                await self.server.docker_network_name(),
                "paddleocr-vl-webui_paddleocr-network",
            )
            self.assertEqual(await self.server.docker_host_repo_root(), str(self.server.PROJECT_ROOT))

        with patch.object(
            self.server,
            "docker_inspect_self",
            new=AsyncMock(
                return_value={
                    "NetworkSettings": {
                        "Networks": {
                            "other": {},
                            "project_paddleocr-network": {},
                        }
                    }
                }
            ),
        ):
            self.assertEqual(await self.server.docker_network_name(), "project_paddleocr-network")

        with patch.object(
            self.server,
            "docker_inspect_self",
            new=AsyncMock(
                return_value={"NetworkSettings": {"Networks": {"first": {}, "second": {}}}}
            ),
        ):
            self.assertEqual(await self.server.docker_network_name(), "first")

        for destination, source in (
            ("/app/static", "/host/repo/static"),
            ("/app/server.py", "/host/repo/server.py"),
        ):
            with patch.object(
                self.server,
                "docker_inspect_self",
                new=AsyncMock(
                    return_value={"Mounts": [{"Destination": destination, "Source": source}]}
                ),
            ):
                self.assertEqual(
                    await self.server.docker_host_repo_root(),
                    str(Path(source).parent),
                )

        # The release overlay mounts only ./data into the immutable WebUI
        # image, so Docker inspect has no /app/static or /app/server.py bind.
        with patch.object(
            self.server,
            "docker_inspect_self",
            new=AsyncMock(
                return_value={
                    "Mounts": [
                        {
                            "Type": "bind",
                            "Source": "/host/release-checkout/data",
                            "Destination": "/app/data",
                            "Mode": "rw",
                            "RW": True,
                        }
                    ]
                }
            ),
        ):
            self.assertEqual(
                await self.server.docker_host_repo_root(),
                str(Path("/host/release-checkout/data").parent),
            )

    async def test_container_configuration_helpers_and_payloads(self):
        self.assertEqual(self.server.bind_path("/host", "file", "/app/file", True), "/host/file:/app/file:ro")
        self.assertEqual(self.server.bind_path("/host", "file", "/app/file"), "/host/file:/app/file")
        self.assertEqual(
            self.server.model_device_requests()[0]["DeviceIDs"],
            [self.server.PANDOCR_GPU_DEVICE_ID],
        )
        self.assertEqual(self.server.healthcheck("test", 2)["StartPeriod"], 2_000_000_000)

        basic = self.server.host_config(network_name="network", binds=[])
        extended = self.server.host_config(
            network_name="network",
            binds=["a:b"],
            port_bindings={"80/tcp": []},
            shm_size=123,
        )
        self.assertNotIn("PortBindings", basic)
        self.assertNotIn("ShmSize", basic)
        self.assertEqual(extended["PortBindings"], {"80/tcp": []})
        self.assertEqual(extended["ShmSize"], 123)

        for service in (
            "paddleocr-vlm-server",
            "paddleocr-vl-api",
            "paddleocr-ocr-api",
            "unlimited-ocr-api",
            "unlimited-ocr-sglang",
            "ovisocr2-api",
        ):
            payload = self.server.container_payload_for(
                service,
                host_root="/host",
                network_name="network",
            )
            self.assertEqual(payload["Image"], self.server.docker_image_name_for(service))
            self.assertEqual(payload["HostConfig"]["NetworkMode"], "network")
        with self.assertRaises(ValueError):
            self.server.container_payload_for("unknown", host_root="/host", network_name="network")

    async def test_container_creation_and_service_creation(self):
        with (
            patch.object(
                self.server,
                "inspect_container",
                new=AsyncMock(return_value={"exists": True}),
            ),
            patch.object(self.server, "docker_api_request", new=AsyncMock()) as request_mock,
        ):
            await self.server.docker_create_container("worker", {})
            request_mock.assert_not_awaited()

        for status, should_raise in ((201, False), (409, True)):
            with (
                patch.object(
                    self.server,
                    "inspect_container",
                    new=AsyncMock(return_value={"exists": False}),
                ),
                patch.object(
                    self.server,
                    "docker_api_request",
                    new=AsyncMock(return_value=response(status, text="create failed")),
                ),
            ):
                if should_raise:
                    with self.assertRaisesRegex(RuntimeError, "Docker create failed"):
                        await self.server.docker_create_container("worker", {})
                else:
                    await self.server.docker_create_container("worker", {})

        with (
            patch.object(
                self.server,
                "docker_network_name",
                new=AsyncMock(return_value="network"),
            ),
            patch.object(
                self.server,
                "docker_host_repo_root",
                new=AsyncMock(return_value="/host"),
            ),
            patch.object(self.server, "docker_create_container", new=AsyncMock()),
            patch.object(self.server, "docker_pull_image", new=AsyncMock()) as pull,
            patch.object(self.server, "docker_build_image", new=AsyncMock()) as build,
        ):
            await self.server.ensure_runtime_service_created("paddleocr-vl-api")
            pull.assert_awaited_once()
            build.assert_not_awaited()

        with (
            patch.object(
                self.server,
                "docker_network_name",
                new=AsyncMock(return_value="network"),
            ),
            patch.object(
                self.server,
                "docker_host_repo_root",
                new=AsyncMock(return_value="/host"),
            ),
            patch.object(self.server, "docker_create_container", new=AsyncMock()),
            patch.object(self.server, "docker_pull_image", new=AsyncMock()) as pull,
            patch.object(self.server, "docker_build_image", new=AsyncMock()) as build,
        ):
            await self.server.ensure_runtime_service_created("ovisocr2-api")
            build.assert_awaited_once()
            pull.assert_not_awaited()

        with (
            patch.object(self.server, "docker_network_name", new=AsyncMock(return_value="network")),
            patch.object(self.server, "docker_host_repo_root", new=AsyncMock(return_value="/host")),
            patch.object(self.server, "docker_create_container", new=AsyncMock()),
            patch.object(self.server, "docker_pull_image", new=AsyncMock()) as pull,
            patch.object(self.server, "docker_build_image", new=AsyncMock()) as build,
        ):
            await self.server.ensure_runtime_service_created("hpd-parsing-server")
            pull.assert_awaited_once()
            build.assert_not_awaited()

    async def test_model_service_lists_and_creation(self):
        self.assertEqual(
            self.server.services_for_model_deploy("unlimited-ocr", "sglang"),
            ["unlimited-ocr-sglang", "unlimited-ocr-api"],
        )
        self.assertEqual(
            self.server.services_for_model_deploy("hpd-parsing"),
            ["hpd-parsing-server", "hpd-parsing-api"],
        )
        with self.assertRaises(ValueError):
            self.server.services_for_model_deploy("unknown")

        with (
            patch.object(
                self.server,
                "services_for_model_deploy",
                return_value=["one", "two"],
            ),
            patch.object(
                self.server,
                "ensure_runtime_service_created",
                new=AsyncMock(),
            ) as ensure,
        ):
            await self.server.ensure_model_runtime_created("model")
        self.assertEqual([call.args[0] for call in ensure.await_args_list], ["one", "two"])

    async def test_fetch_health_and_ready_states(self):
        for health_response, expected in (
            (response(200, payload={"status": "ok"}), (True, {"status": "ok"})),
            (response(503, payload=["bad"]), (False, {})),
            (response(200, text="not-json"), (True, {})),
        ):
            client = FakeAsyncClient(get_response=health_response)
            with patch.object(self.server.httpx, "AsyncClient", return_value=client):
                self.assertEqual(await self.server.fetch_http_health("http://health"), expected)

        client = FakeAsyncClient(get_response=RuntimeError("offline"))
        with patch.object(self.server.httpx, "AsyncClient", return_value=client):
            self.assertEqual(await self.server.fetch_http_health("http://health"), (False, {}))

        with patch.object(
            self.server,
            "fetch_http_health",
            new=AsyncMock(return_value=(True, {})),
        ):
            self.assertTrue(await self.server.check_http_health("http://health"))

        self.assertEqual(self.server.model_health_ready_state("model", False, {}), (False, "unknown"))
        with patch.object(self.server, "unlimited_ocr_runtime_backend", "sglang"):
            self.assertEqual(
                self.server.model_health_ready_state(
                    "unlimited-ocr",
                    True,
                    {"sglang": {"ready": False}},
                ),
                (False, "starting"),
            )
            self.assertEqual(
                self.server.model_health_ready_state(
                    "unlimited-ocr",
                    True,
                    {"sglang": {"ready": True}},
                ),
                (True, "ready"),
            )
        with patch.object(self.server, "unlimited_ocr_runtime_backend", "transformers"):
            cases = (
                ({"transformers": {"modelError": "bad"}}, (False, "error")),
                ({"transformers": {"preloadEnabled": True, "modelLoaded": True}}, (True, "ready")),
                ({"transformers": {"preloadEnabled": True, "modelLoading": True}}, (False, "warming")),
                ({"transformers": {"preloadEnabled": True}}, (False, "starting")),
                ({"transformers": {"preloadEnabled": False}}, (True, "ready")),
            )
            for data, expected in cases:
                self.assertEqual(
                    self.server.model_health_ready_state("unlimited-ocr", True, data),
                    expected,
                )

    async def test_runtime_status_covers_external_and_docker_states(self):
        config = {"containers": ["one", "two"], "health_url": "http://health"}
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, {"test": config}, clear=True),
            patch.object(self.server, "model_control_available", return_value=False),
            patch.object(
                self.server,
                "inspect_container",
                new=AsyncMock(
                    side_effect=[
                        {"exists": False, "running": False},
                        {"exists": False, "running": False},
                    ]
                ),
            ),
            patch.object(
                self.server,
                "fetch_http_health",
                new=AsyncMock(return_value=(True, {})),
            ),
        ):
            status = await self.server.model_runtime_status("test")
        self.assertTrue(status["ready"])
        self.assertEqual(status["state"], "ready")

        docker_cases = (
            (
                [
                    {"exists": False, "running": False},
                    {"exists": True, "running": False},
                ],
                (False, {}),
                "missing",
            ),
            (
                [
                    {"exists": True, "running": True},
                    {"exists": True, "running": True},
                ],
                (True, {}),
                "ready",
            ),
            (
                [
                    {"exists": True, "running": True},
                    {"exists": True, "running": False},
                ],
                (False, {}),
                "partial",
            ),
            (
                [
                    {"exists": True, "running": False},
                    {"exists": True, "running": False},
                ],
                (False, {}),
                "stopped",
            ),
        )
        for containers, health, expected_state in docker_cases:
            with (
                patch.dict(self.server.MODEL_RUNTIME_CONFIG, {"test": config}, clear=True),
                patch.object(self.server, "model_control_available", return_value=True),
                patch.object(
                    self.server,
                    "inspect_container",
                    new=AsyncMock(side_effect=containers),
                ),
                patch.object(
                    self.server,
                    "fetch_http_health",
                    new=AsyncMock(return_value=health),
                ),
            ):
                status = await self.server.model_runtime_status("test")
            self.assertEqual(status["state"], expected_state)

    async def test_runtime_payload_and_unlimited_enrichment(self):
        plain = {"id": "x"}
        self.assertIs(await self.server.enrich_unlimited_ocr_runtime_status("other", plain), plain)
        with (
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(
                self.server,
                "inspect_container",
                new=AsyncMock(return_value={"exists": True}),
            ),
        ):
            enriched = await self.server.enrich_unlimited_ocr_runtime_status(
                "unlimited-ocr",
                {},
            )
        self.assertIn("sglangContainer", enriched)

        with (
            patch.dict(
                self.server.MODEL_RUNTIME_CONFIG,
                {"ready": {}, "running": {}, "down": {}},
                clear=True,
            ),
            patch.dict(self.server.MODEL_RUNTIME_CONTAINER_GROUPS, {}, clear=True),
            patch.object(
                self.server,
                "model_runtime_status",
                new=AsyncMock(
                    side_effect=[
                        {"ready": True, "running": True},
                        {"ready": False, "running": True},
                        {"ready": False, "running": False},
                    ]
                ),
            ),
            patch.object(self.server, "model_control_available", return_value=True),
        ):
            payload = await self.server.build_model_runtime_payload()
        self.assertEqual(payload["activeModelId"], "ready")
        self.assertTrue(payload["controlAvailable"])
        self.assertEqual(payload["runningModelIds"], ["ready", "running"])
        self.assertEqual(payload["readyModelIds"], ["ready"])
        self.assertTrue(payload["exclusivityViolation"])
        self.assertEqual(payload["controllerOcrLeaseCount"], 0)

    async def test_wait_helpers_cover_ready_missing_and_timeout(self):
        with patch.object(
            self.server,
            "model_runtime_status",
            new=AsyncMock(return_value={"ready": True}),
        ):
            await self.server.wait_model_ready("model", 10)
        with (
            patch.object(self.server.time, "monotonic", side_effect=[0, 2]),
            patch.object(self.server.asyncio, "sleep", new=AsyncMock()),
        ):
            with self.assertRaises(TimeoutError):
                await self.server.wait_model_ready("model", 1)

        with patch.object(
            self.server,
            "inspect_container",
            new=AsyncMock(
                return_value={"exists": True, "running": True, "health": "healthy"}
            ),
        ):
            await self.server.wait_container_runtime_ready("worker", 10)
        with patch.object(
            self.server,
            "inspect_container",
            new=AsyncMock(
                return_value={"exists": False, "running": False, "health": "missing"}
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "is missing"):
                await self.server.wait_container_runtime_ready("worker", 10)
        with (
            patch.object(self.server.time, "monotonic", side_effect=[0, 2]),
            patch.object(self.server.asyncio, "sleep", new=AsyncMock()),
        ):
            with self.assertRaises(TimeoutError):
                await self.server.wait_container_runtime_ready("worker", 1)

        with (
            patch.object(
                self.server,
                "model_runtime_status",
                new=AsyncMock(
                    return_value={
                        "ready": True,
                        "unlimitedOcrBackend": "sglang",
                    }
                ),
            ),
        ):
            await self.server.wait_unlimited_ocr_backend_ready("sglang", 10)
        with (
            patch.object(self.server.time, "monotonic", side_effect=[0, 2]),
            patch.object(self.server.asyncio, "sleep", new=AsyncMock()),
        ):
            with self.assertRaises(TimeoutError):
                await self.server.wait_unlimited_ocr_backend_ready("sglang", 1)

        with patch.object(
            self.server,
            "fetch_http_health",
            new=AsyncMock(return_value=(True, {})),
        ):
            await self.server.wait_unlimited_ocr_adapter_http(10)
        with (
            patch.object(self.server.time, "monotonic", side_effect=[0, 2]),
            patch.object(self.server.asyncio, "sleep", new=AsyncMock()),
        ):
            with self.assertRaises(TimeoutError):
                await self.server.wait_unlimited_ocr_adapter_http(1)

    async def test_adapter_control_handles_json_empty_and_failure(self):
        self.assertEqual(
            self.server.unlimited_ocr_adapter_base_url(),
            self.server.UNLIMITED_OCR_SERVICE_URL.rsplit("/", 1)[0],
        )
        for control_response, expected in (
            (response(200, payload={"ok": True}), {"ok": True}),
            (response(200, payload=["not", "dict"]), {}),
            (response(200, text="not-json"), {}),
        ):
            client = FakeAsyncClient(post_response=control_response)
            with patch.object(self.server.httpx, "AsyncClient", return_value=client):
                result = await self.server.call_unlimited_ocr_adapter_control(
                    "/control",
                    timeout=5,
                )
            self.assertEqual(result, expected)
        client = FakeAsyncClient(post_response=response(500, text="failed"))
        with patch.object(self.server.httpx, "AsyncClient", return_value=client):
            with self.assertRaisesRegex(RuntimeError, "control failed"):
                await self.server.call_unlimited_ocr_adapter_control("/control")

    async def test_ensure_unlimited_backend_runtime_covers_both_backends(self):
        sglang_events = []

        async def sglang_control(path, **_kwargs):
            sglang_events.append(path)

        async def sglang_ensure(_name):
            sglang_events.append("ensure-created")

        async def sglang_release(_model_id, **_kwargs):
            sglang_events.append("gpu-release")

        async def sglang_action(name, operation):
            sglang_events.append(f"{operation}:{name}")

        with (
            patch.object(
                self.server,
                "wait_unlimited_ocr_adapter_http",
                new=AsyncMock(),
            ) as wait_http,
            patch.object(
                self.server,
                "call_unlimited_ocr_adapter_control",
                new=AsyncMock(side_effect=sglang_control),
            ) as control,
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(
                self.server,
                "assert_non_target_model_containers_stopped",
                new=AsyncMock(),
            ) as non_target_stopped,
            patch.object(
                self.server,
                "ensure_runtime_service_created",
                new=AsyncMock(side_effect=sglang_ensure),
            ) as ensure,
            patch.object(
                self.server,
                "docker_container_action",
                new=AsyncMock(side_effect=sglang_action),
            ) as action,
            patch.object(
                self.server,
                "wait_for_stable_gpu_release",
                new=AsyncMock(side_effect=sglang_release),
            ) as release_gate,
            patch.object(
                self.server,
                "wait_container_runtime_ready",
                new=AsyncMock(),
            ) as wait_container,
            patch.object(
                self.server,
                "wait_unlimited_ocr_backend_ready",
                new=AsyncMock(),
            ) as wait_backend,
        ):
            await self.server.ensure_unlimited_ocr_backend_runtime("sglang", 100)
        wait_http.assert_awaited_once()
        non_target_stopped.assert_awaited_once_with("unlimited-ocr")
        self.assertEqual(control.await_args.args[0], "/backend/transformers/unload")
        ensure.assert_awaited_once_with("unlimited-ocr-sglang")
        release_gate.assert_awaited_once_with("unlimited-ocr", timeout=100)
        action.assert_awaited_once_with("unlimited-ocr-sglang", "start")
        wait_container.assert_awaited_once()
        wait_backend.assert_awaited_once_with("sglang", 100)
        self.assertEqual(
            sglang_events,
            [
                "/backend/transformers/unload",
                "ensure-created",
                "gpu-release",
                "start:unlimited-ocr-sglang",
            ],
        )

        transformers_events = []

        async def transformers_action(name, operation):
            transformers_events.append(f"{operation}:{name}")

        async def transformers_stopped(_containers, **_kwargs):
            transformers_events.append("stopped-twice")

        async def transformers_release(_model_id, **_kwargs):
            transformers_events.append("gpu-release")

        async def transformers_control(path, **_kwargs):
            transformers_events.append(path)

        with (
            patch.object(
                self.server,
                "wait_unlimited_ocr_adapter_http",
                new=AsyncMock(),
            ),
            patch.object(
                self.server,
                "call_unlimited_ocr_adapter_control",
                new=AsyncMock(side_effect=transformers_control),
            ) as control,
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(
                self.server,
                "assert_non_target_model_containers_stopped",
                new=AsyncMock(),
            ) as non_target_stopped,
            patch.object(
                self.server,
                "docker_container_action",
                new=AsyncMock(side_effect=transformers_action),
            ) as action,
            patch.object(
                self.server,
                "assert_containers_stopped_twice",
                new=AsyncMock(side_effect=transformers_stopped),
            ) as stopped_twice,
            patch.object(
                self.server,
                "wait_for_stable_gpu_release",
                new=AsyncMock(side_effect=transformers_release),
            ) as release_gate,
            patch.object(
                self.server,
                "wait_unlimited_ocr_backend_ready",
                new=AsyncMock(),
            ) as wait_backend,
        ):
            await self.server.ensure_unlimited_ocr_backend_runtime("transformers", 100)
        action.assert_awaited_once_with("unlimited-ocr-sglang", "stop")
        non_target_stopped.assert_awaited_once_with("unlimited-ocr")
        stopped_twice.assert_awaited_once()
        release_gate.assert_awaited_once_with("unlimited-ocr", timeout=100)
        control.assert_awaited_once_with("/backend/transformers/preload", timeout=100)
        wait_backend.assert_awaited_once_with("transformers", 100)
        self.assertEqual(
            transformers_events,
            [
                "stop:unlimited-ocr-sglang",
                "stopped-twice",
                "gpu-release",
                "/backend/transformers/preload",
            ],
        )

    async def test_activate_model_runtime_success_unknown_unavailable_and_error(self):
        with self.assertRaises(ValueError):
            await self.server.activate_model_runtime("unknown")
        with (
            patch.dict(
                self.server.MODEL_RUNTIME_CONFIG,
                {"target": {"stop_order": [], "start_order": []}},
                clear=True,
            ),
            patch.object(self.server, "model_control_available", return_value=False),
        ):
            with self.assertRaises(RuntimeError):
                await self.server.activate_model_runtime("target")

        config = {
            "other": {"stop_order": ["old"], "start_order": []},
            "target": {"stop_order": [], "start_order": ["new"]},
        }
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.dict(self.server.MODEL_RUNTIME_CONTAINER_GROUPS, {}, clear=True),
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(
                self.server,
                "docker_container_action",
                new=AsyncMock(),
            ) as action,
            patch.object(
                self.server,
                "wait_container_runtime_ready",
                new=AsyncMock(),
            ),
            patch.object(self.server, "wait_model_ready", new=AsyncMock()),
            patch.object(
                self.server,
                "assert_non_target_model_containers_stopped",
                new=AsyncMock(),
            ),
            patch.object(
                self.server,
                "assert_containers_stopped_twice",
                new=AsyncMock(),
            ),
            patch.object(
                self.server,
                "assert_target_model_ready_and_exclusive",
                new=AsyncMock(),
            ),
        ):
            await self.server.activate_model_runtime("target")
        self.assertEqual(
            [(call.args[0], call.args[1]) for call in action.await_args_list],
            [("old", "stop"), ("new", "start")],
        )
        self.assertEqual(self.server.model_runtime_operation["state"], "ready")

        config = {
            "unlimited-ocr": {"stop_order": [], "start_order": ["api"]},
        }
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.dict(self.server.MODEL_RUNTIME_CONTAINER_GROUPS, {}, clear=True),
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(
                self.server,
                "docker_container_action",
                new=AsyncMock(side_effect=RuntimeError("start failed")),
            ),
            patch.object(
                self.server,
                "assert_non_target_model_containers_stopped",
                new=AsyncMock(),
            ),
            patch.object(
                self.server,
                "cleanup_model_runtime_containers",
                new=AsyncMock(return_value=["api: cleanup stop failed"]),
            ),
            patch.object(self.server.logger, "exception"),
        ):
            await self.server.activate_model_runtime("unlimited-ocr")
        self.assertEqual(self.server.model_runtime_operation["state"], "error")
        self.assertIn("start failed", self.server.model_runtime_operation["message"])
        self.assertIn("cleanup failed: api: cleanup stop failed", self.server.model_runtime_operation["message"])

        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.dict(self.server.MODEL_RUNTIME_CONTAINER_GROUPS, {}, clear=True),
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(self.server, "docker_container_action", new=AsyncMock()),
            patch.object(
                self.server,
                "wait_container_runtime_ready",
                new=AsyncMock(),
            ),
            patch.object(
                self.server,
                "ensure_unlimited_ocr_backend_runtime",
                new=AsyncMock(),
            ) as ensure,
            patch.object(self.server, "wait_model_ready", new=AsyncMock()),
            patch.object(
                self.server,
                "assert_non_target_model_containers_stopped",
                new=AsyncMock(),
            ),
            patch.object(
                self.server,
                "assert_target_model_ready_and_exclusive",
                new=AsyncMock(),
            ),
        ):
            await self.server.activate_model_runtime("unlimited-ocr")
        ensure.assert_awaited_once()

    async def test_static_container_groups_stop_and_detect_disabled_residual_models(self):
        self.assertEqual(
            set(self.server.MODEL_RUNTIME_CONTAINER_GROUPS),
            {"paddleocr-vl-1.6", "pp-ocrv6", "unlimited-ocr", "ovisocr2", "hpd-parsing"},
        )
        config = {
            "target": {"containers": ["target-api"], "stop_order": ["target-api"]},
        }
        groups = {
            "target": {"containers": ["target-api"], "stop_order": ["target-api"]},
            "disabled": {"containers": ["disabled-worker"], "stop_order": ["disabled-worker"]},
        }
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.dict(self.server.MODEL_RUNTIME_CONTAINER_GROUPS, groups, clear=True),
            patch.object(self.server, "docker_container_action", new=AsyncMock()) as action,
        ):
            await self.server.stop_non_target_model_containers("target")
        action.assert_awaited_once_with("disabled-worker", "stop")

        async def inspect(name):
            return {"name": name, "running": name == "disabled-worker"}

        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.dict(self.server.MODEL_RUNTIME_CONTAINER_GROUPS, groups, clear=True),
            patch.object(self.server, "inspect_container", new=AsyncMock(side_effect=inspect)),
        ):
            with self.assertRaisesRegex(RuntimeError, "disabled:disabled-worker"):
                await self.server.assert_non_target_model_containers_stopped("target")

        stopped_inspect = AsyncMock(return_value={"running": False})
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.dict(self.server.MODEL_RUNTIME_CONTAINER_GROUPS, groups, clear=True),
            patch.object(self.server, "inspect_container", new=stopped_inspect),
            patch.object(self.server.asyncio, "sleep", new=AsyncMock()) as sample_sleep,
        ):
            await self.server.assert_non_target_model_containers_stopped("target")
        self.assertEqual(stopped_inspect.await_count, 2)
        sample_sleep.assert_awaited_once_with(self.server.GPU_RELEASE_SAMPLE_INTERVAL_SECONDS)

        error_groups = {
            "target": {"stop_order": ["target-api"]},
            "old-a": {"stop_order": ["old-a"]},
            "old-b": {"stop_order": ["old-b"]},
        }

        async def stop_with_first_error(name, _operation):
            if name == "old-a":
                raise RuntimeError("stop failed")

        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, {"target": {}}, clear=True),
            patch.dict(self.server.MODEL_RUNTIME_CONTAINER_GROUPS, error_groups, clear=True),
            patch.object(
                self.server,
                "docker_container_action",
                new=AsyncMock(side_effect=stop_with_first_error),
            ) as action,
            patch.object(self.server.logger, "exception"),
        ):
            with self.assertRaisesRegex(RuntimeError, "old-a: stop failed"):
                await self.server.stop_non_target_model_containers("target")
        self.assertEqual([call.args[0] for call in action.await_args_list], ["old-a", "old-b"])

    async def test_switch_failure_cleans_started_target_and_final_verification_is_strict(self):
        config = {
            "old": {"containers": ["old"], "stop_order": ["old"], "start_order": []},
            "target": {"containers": ["new"], "stop_order": ["new"], "start_order": ["new"]},
        }
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.dict(self.server.MODEL_RUNTIME_CONTAINER_GROUPS, {}, clear=True),
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(self.server, "docker_container_action", new=AsyncMock()) as action,
            patch.object(
                self.server,
                "wait_container_runtime_ready",
                new=AsyncMock(side_effect=RuntimeError("warmup failed")),
            ),
            patch.object(
                self.server,
                "assert_non_target_model_containers_stopped",
                new=AsyncMock(),
            ),
            patch.object(
                self.server,
                "assert_containers_stopped_twice",
                new=AsyncMock(),
            ),
            patch.object(
                self.server,
                "model_failure_diagnostics",
                new=AsyncMock(return_value={}),
            ),
            patch.object(self.server.logger, "exception"),
        ):
            activated = await self.server.activate_model_runtime("target")
        self.assertFalse(activated)
        self.assertEqual(
            [(call.args[0], call.args[1]) for call in action.await_args_list],
            [("old", "stop"), ("new", "stop"), ("new", "start"), ("new", "stop")],
        )
        self.assertEqual(self.server.model_runtime_operation["state"], "error")

        statuses = {
            "target": {"running": True, "ready": True},
            "other": {"running": True, "ready": False},
        }

        async def runtime_status(model_id):
            return statuses[model_id]

        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, {"target": {}, "other": {}}, clear=True),
            patch.dict(self.server.MODEL_RUNTIME_CONTAINER_GROUPS, {}, clear=True),
            patch.object(self.server, "model_runtime_status", new=AsyncMock(side_effect=runtime_status)),
            patch.object(self.server, "model_control_available", return_value=True),
        ):
            with self.assertRaisesRegex(RuntimeError, "running=target,other"):
                await self.server.assert_target_model_ready_and_exclusive("target")

    async def test_switch_stops_other_models_before_gpu_preflight(self):
        events = []
        running = {"old": True, "new": False}
        runtime_samples = []
        config = {
            "old": {"stop_order": ["old"], "start_order": []},
            "target": {
                "stop_order": ["new"],
                "start_order": ["new"],
                "gpu_memory": {"minimum_mib": 1, "minimum_free_mib": 1},
            },
        }

        async def action(name, operation):
            if operation == "stop":
                running[name] = False
            elif operation == "start":
                self.assertFalse(running["old"], "target started while old model was still resident")
                running[name] = True
            events.append(f"{operation}:{name}")
            runtime_samples.append(frozenset(name for name, active in running.items() if active))

        async def assert_stopped(_model_id):
            self.assertFalse(running["old"])
            events.append("assert-stopped")

        async def preflight(_model_id):
            events.append("gpu-preflight")

        async def release_gate(_model_id, **_kwargs):
            events.append("gpu-release")

        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.dict(self.server.MODEL_RUNTIME_CONTAINER_GROUPS, {}, clear=True),
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(self.server, "docker_container_action", new=AsyncMock(side_effect=action)),
            patch.object(
                self.server,
                "assert_non_target_model_containers_stopped",
                new=AsyncMock(side_effect=assert_stopped),
            ),
            patch.object(
                self.server,
                "ensure_model_gpu_compatible",
                new=AsyncMock(side_effect=preflight),
            ),
            patch.object(
                self.server,
                "wait_for_stable_gpu_release",
                new=AsyncMock(side_effect=release_gate),
            ),
            patch.object(self.server, "wait_container_runtime_ready", new=AsyncMock()),
            patch.object(self.server, "wait_model_ready", new=AsyncMock()),
            patch.object(
                self.server,
                "assert_target_model_ready_and_exclusive",
                new=AsyncMock(),
            ),
        ):
            self.assertTrue(await self.server.activate_model_runtime("target"))

        self.assertLess(events.index("stop:old"), events.index("assert-stopped"))
        self.assertLess(events.index("assert-stopped"), events.index("gpu-preflight"))
        self.assertLess(events.index("gpu-preflight"), events.index("gpu-release"))
        self.assertLess(events.index("gpu-release"), events.index("start:new"))
        self.assertTrue(all(len(sample) <= 1 for sample in runtime_samples))
        self.assertEqual(runtime_samples, [frozenset(), frozenset({"new"})])

    async def test_switch_release_gate_failure_never_starts_target(self):
        config = {
            "old": {"stop_order": ["old"], "start_order": []},
            "target": {
                "stop_order": ["new"],
                "start_order": ["new"],
                "gpu_memory": {"minimum_mib": 1, "minimum_free_mib": 1},
            },
        }
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.dict(self.server.MODEL_RUNTIME_CONTAINER_GROUPS, {}, clear=True),
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(self.server, "docker_container_action", new=AsyncMock()) as action,
            patch.object(
                self.server,
                "assert_non_target_model_containers_stopped",
                new=AsyncMock(),
            ),
            patch.object(self.server, "ensure_model_gpu_compatible", new=AsyncMock()),
            patch.object(
                self.server,
                "wait_for_stable_gpu_release",
                new=AsyncMock(side_effect=RuntimeError("release evidence unavailable")),
            ),
            patch.object(self.server, "model_failure_diagnostics", new=AsyncMock(return_value={})),
            patch.object(self.server.logger, "exception"),
        ):
            activated = await self.server.activate_model_runtime("target")

        self.assertFalse(activated)
        self.assertEqual(
            [(call.args[0], call.args[1]) for call in action.await_args_list],
            [("old", "stop")],
        )
        self.assertIn("release evidence unavailable", self.server.model_runtime_operation["message"])

    async def test_idempotent_unique_ready_target_skips_release_gate_and_restart(self):
        config = {
            "other": {"stop_order": ["old"], "start_order": []},
            "target": {
                "containers": ["new"],
                "stop_order": ["new"],
                "start_order": ["new"],
                "health_url": "http://target/health",
                "gpu_memory": {"minimum_mib": 1, "minimum_free_mib": 1},
            },
        }
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.dict(self.server.MODEL_RUNTIME_CONTAINER_GROUPS, {}, clear=True),
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(self.server, "docker_container_action", new=AsyncMock()) as action,
            patch.object(
                self.server,
                "assert_non_target_model_containers_stopped",
                new=AsyncMock(),
            ),
            patch.object(
                self.server,
                "target_model_is_unique_running_and_ready",
                new=AsyncMock(return_value=True),
            ),
            patch.object(
                self.server,
                "assert_target_model_ready_and_exclusive",
                new=AsyncMock(),
            ) as final_assert,
            patch.object(self.server, "ensure_model_gpu_compatible", new=AsyncMock()) as total_gate,
            patch.object(
                self.server,
                "wait_for_stable_gpu_release",
                new=AsyncMock(),
            ) as release_gate,
        ):
            activated = await self.server.activate_model_runtime("target")

        self.assertTrue(activated)
        self.assertEqual(
            [(call.args[0], call.args[1]) for call in action.await_args_list],
            [("old", "stop")],
        )
        final_assert.assert_awaited_once_with("target")
        total_gate.assert_not_awaited()
        release_gate.assert_not_awaited()

        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.dict(self.server.MODEL_RUNTIME_CONTAINER_GROUPS, {}, clear=True),
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(self.server, "docker_container_action", new=AsyncMock()),
            patch.object(
                self.server,
                "assert_non_target_model_containers_stopped",
                new=AsyncMock(),
            ),
            patch.object(
                self.server,
                "target_model_is_unique_running_and_ready",
                new=AsyncMock(return_value=True),
            ),
            patch.object(
                self.server,
                "assert_target_model_ready_and_exclusive",
                new=AsyncMock(side_effect=RuntimeError("ready target changed")),
            ),
            patch.object(
                self.server,
                "cleanup_model_runtime_containers",
                new=AsyncMock(return_value=[]),
            ) as cleanup,
            patch.object(self.server, "model_failure_diagnostics", new=AsyncMock(return_value={})),
            patch.object(self.server.logger, "exception"),
        ):
            activated = await self.server.activate_model_runtime("target")
        self.assertFalse(activated)
        cleanup.assert_awaited_once_with("target")

    async def test_controller_ocr_lease_and_switch_are_atomic_and_exclusive(self):
        config = {"target": {}}
        groups = {"target": {"containers": ["target-api"]}}

        async def runtime_status(_model_id):
            return {"running": True, "ready": True}

        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.dict(self.server.MODEL_RUNTIME_CONTAINER_GROUPS, groups, clear=True),
            patch.object(self.server, "model_runtime_status", new=AsyncMock(side_effect=runtime_status)),
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(self.server, "activate_model_runtime", new=AsyncMock(return_value=True)),
            patch.object(self.server, "CONTROLLER_OCR_LEASE_TTL_SECONDS", 0),
        ):
            lease = await self.server.acquire_controller_ocr_lease("target")
            self.assertIsNone(lease["expiresAt"])
            with self.assertRaises(HTTPException) as leased_switch:
                await self.server.schedule_model_runtime_activation("target")
            self.assertEqual(leased_switch.exception.status_code, 409)
            self.assertTrue(await self.server.release_controller_ocr_lease(lease["leaseId"]))

            async def acquire_attempt():
                try:
                    return "lease", await self.server.acquire_controller_ocr_lease("target")
                except HTTPException as error:
                    return "lease_error", error.status_code

            async def switch_attempt():
                try:
                    await self.server.schedule_model_runtime_activation("target")
                    return "switch", 200
                except HTTPException as error:
                    return "switch_error", error.status_code

            results = await asyncio.gather(acquire_attempt(), switch_attempt())
            successes = [result for result in results if result[0] in {"lease", "switch"}]
            failures = [result for result in results if result[0].endswith("_error")]
            self.assertEqual(len(successes), 1)
            self.assertEqual(failures[0][1], 409)
            if successes[0][0] == "lease":
                await self.server.release_controller_ocr_lease(successes[0][1]["leaseId"])
            elif isinstance(self.server.model_runtime_task, asyncio.Task):
                await self.server.model_runtime_task

        violation_statuses = {
            "target": {"running": True, "ready": True},
            "other": {"running": True, "ready": True},
        }

        async def violating_status(model_id):
            return violation_statuses[model_id]

        self.server.model_runtime_task = None
        self.server.set_model_runtime_operation("idle", "", "target")
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, {"target": {}, "other": {}}, clear=True),
            patch.dict(self.server.MODEL_RUNTIME_CONTAINER_GROUPS, {}, clear=True),
            patch.object(self.server, "model_runtime_status", new=AsyncMock(side_effect=violating_status)),
            patch.object(self.server, "model_control_available", return_value=True),
        ):
            with self.assertRaises(HTTPException) as violation:
                await self.server.acquire_controller_ocr_lease("target")
        self.assertEqual(violation.exception.status_code, 409)
        self.assertIn("exclusivity violation", str(violation.exception.detail).lower())

    async def test_controller_lease_guard_expiry_and_cleanup_error_branches(self):
        with patch.dict(self.server.os.environ, {"LEASE_TTL": "invalid"}):
            self.assertEqual(self.server.parse_nonnegative_int_env("LEASE_TTL", "7"), 7)
        with patch.dict(self.server.os.environ, {"LEASE_TTL": "-3"}):
            self.assertEqual(self.server.parse_nonnegative_int_env("LEASE_TTL", "7"), 0)

        self.server.controller_ocr_leases.update(
            {
                "expired": {"modelId": "target", "expiresAt": 5},
                "permanent": {"modelId": "target", "expiresAt": None},
            }
        )
        with patch.object(self.server.logger, "warning") as warning:
            self.server.prune_controller_ocr_leases(now=10)
        self.assertNotIn("expired", self.server.controller_ocr_leases)
        self.assertIn("permanent", self.server.controller_ocr_leases)
        warning.assert_called_once()
        self.server.controller_ocr_leases.clear()

        residual_groups = {
            "target": {"containers": ["target-api"]},
            "disabled": {"containers": ["disabled-cold", "disabled-hot"]},
        }
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, {"target": {}}, clear=True),
            patch.dict(self.server.MODEL_RUNTIME_CONTAINER_GROUPS, residual_groups, clear=True),
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(
                self.server,
                "inspect_container",
                new=AsyncMock(side_effect=[{"running": False}, {"running": True}]),
            ),
        ):
            residual = await self.server.runtime_exclusivity_snapshot(
                {"target": {"running": True, "ready": True}}
            )
        self.assertEqual(residual["runningModelIds"], ["target", "disabled"])
        self.assertTrue(residual["exclusivityViolation"])

        config = {"target": {}}
        with patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True):
            with self.assertRaises(HTTPException) as unknown:
                await self.server.acquire_controller_ocr_lease("missing")
        self.assertEqual(unknown.exception.status_code, 400)

        self.server.model_runtime_task = FakeTask(done=False)
        with patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True):
            with self.assertRaises(HTTPException) as runtime_busy:
                await self.server.acquire_controller_ocr_lease("target")
        self.assertEqual(runtime_busy.exception.status_code, 409)

        self.server.model_runtime_task = None
        self.server.unlimited_ocr_backend_task = FakeTask(done=False)
        with patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True):
            with self.assertRaises(HTTPException) as backend_busy:
                await self.server.acquire_controller_ocr_lease("target")
        self.assertEqual(backend_busy.exception.status_code, 409)

        self.server.unlimited_ocr_backend_task = None
        self.server.set_model_runtime_operation("switching", "busy", "target")
        with patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True):
            with self.assertRaises(HTTPException) as operation_busy:
                await self.server.acquire_controller_ocr_lease("target")
        self.assertEqual(operation_busy.exception.status_code, 409)
        self.server.set_model_runtime_operation("idle", "", "target")

        self.server.controller_ocr_leases["other"] = {
            "modelId": "other",
            "expiresAt": None,
        }
        with patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True):
            with self.assertRaises(HTTPException) as other_model:
                await self.server.acquire_controller_ocr_lease("target")
        self.assertEqual(other_model.exception.status_code, 409)

        self.server.controller_ocr_leases.clear()
        self.server.controller_ocr_leases["same"] = {
            "modelId": "target",
            "expiresAt": None,
        }
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(self.server, "MAX_CONCURRENT_OCR", 1),
        ):
            with self.assertRaises(HTTPException) as at_limit:
                await self.server.acquire_controller_ocr_lease("target")
        self.assertEqual(at_limit.exception.status_code, 429)
        self.server.controller_ocr_leases.clear()

        not_ready_snapshot = {
            "runningModelIds": ["target"],
            "readyModelIds": [],
            "exclusivityViolation": False,
        }
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(
                self.server,
                "model_runtime_status",
                new=AsyncMock(return_value={"running": True, "ready": False}),
            ),
            patch.object(
                self.server,
                "runtime_exclusivity_snapshot",
                new=AsyncMock(return_value=not_ready_snapshot),
            ),
        ):
            with self.assertRaises(HTTPException) as not_ready:
                await self.server.acquire_controller_ocr_lease("target")
        self.assertEqual(not_ready.exception.status_code, 503)

        not_unique_snapshot = {
            "runningModelIds": [],
            "readyModelIds": ["target"],
            "exclusivityViolation": False,
        }
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(
                self.server,
                "model_runtime_status",
                new=AsyncMock(return_value={"running": False, "ready": True}),
            ),
            patch.object(
                self.server,
                "runtime_exclusivity_snapshot",
                new=AsyncMock(return_value=not_unique_snapshot),
            ),
        ):
            with self.assertRaises(HTTPException) as not_unique:
                await self.server.acquire_controller_ocr_lease("target")
        self.assertEqual(not_unique.exception.status_code, 503)

        unique_snapshot = {
            "runningModelIds": ["target"],
            "readyModelIds": ["target"],
            "exclusivityViolation": False,
        }
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(
                self.server,
                "model_runtime_status",
                new=AsyncMock(return_value={"running": True, "ready": True}),
            ),
            patch.object(
                self.server,
                "runtime_exclusivity_snapshot",
                new=AsyncMock(return_value=unique_snapshot),
            ),
            patch.object(self.server, "CONTROLLER_OCR_LEASE_TTL_SECONDS", 5),
            patch.object(self.server.time, "time", return_value=100),
        ):
            expiring = await self.server.acquire_controller_ocr_lease("target")
            self.assertEqual(expiring["expiresAt"], 105)
            self.assertTrue(await self.server.release_controller_ocr_lease(expiring["leaseId"]))

        cleanup_config = {"target": {"stop_order": ["target-a", "target-b"]}}

        async def cleanup_action(name, _operation):
            if name == "target-a":
                raise RuntimeError("cleanup failed")

        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, cleanup_config, clear=True),
            patch.dict(self.server.MODEL_RUNTIME_CONTAINER_GROUPS, {}, clear=True),
            patch.object(
                self.server,
                "docker_container_action",
                new=AsyncMock(side_effect=cleanup_action),
            ),
            patch.object(self.server.logger, "exception") as cleanup_log,
        ):
            cleanup_errors = await self.server.cleanup_model_runtime_containers("target")
        self.assertEqual(cleanup_errors, ["target-a: cleanup failed"])
        cleanup_log.assert_called_once()

    async def test_schedule_model_activation_all_guards_and_replacement(self):
        with self.assertRaises(HTTPException) as unknown:
            await self.server.schedule_model_runtime_activation("unknown")
        self.assertEqual(unknown.exception.status_code, 400)

        config = {"target": {"stop_order": [], "start_order": []}}
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(self.server, "model_control_available", return_value=False),
        ):
            with self.assertRaises(HTTPException) as unavailable:
                await self.server.schedule_model_runtime_activation("target")
        self.assertEqual(unavailable.exception.status_code, 503)

        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(self.server, "ocr_active_count", 1),
        ):
            with self.assertRaises(HTTPException) as busy:
                await self.server.schedule_model_runtime_activation("target")
        self.assertEqual(busy.exception.status_code, 409)

        previous = FakeTask(done=False)
        created = FakeTask()

        def create_task(coroutine):
            coroutine.close()
            return created

        self.server.model_runtime_task = previous
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(self.server.asyncio, "create_task", side_effect=create_task),
        ):
            with self.assertRaises(HTTPException) as already_busy:
                await self.server.schedule_model_runtime_activation("target")
        self.assertEqual(already_busy.exception.status_code, 409)
        self.assertFalse(previous.cancelled)

        self.server.model_runtime_task = None
        self.server.unlimited_ocr_backend_task = FakeTask(done=False)
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(self.server, "model_control_available", return_value=True),
        ):
            with self.assertRaises(HTTPException) as backend_busy:
                await self.server.schedule_model_runtime_activation("target")
        self.assertEqual(backend_busy.exception.status_code, 409)

        self.server.unlimited_ocr_backend_task = None
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(self.server.asyncio, "create_task", side_effect=create_task),
        ):
            await self.server.schedule_model_runtime_activation("target")
        self.assertIs(self.server.model_runtime_task, created)

    async def test_deploy_and_schedule_model_runtime(self):
        with (
            patch.object(self.server, "unlimited_ocr_runtime_backend", "transformers"),
            patch.object(
                self.server,
                "ensure_model_runtime_created",
                new=AsyncMock(),
            ) as ensure,
            patch.object(
                self.server,
                "activate_model_runtime",
                new=AsyncMock(),
            ) as activate,
            patch.object(self.server, "save_runtime_settings") as save,
            patch.object(
                self.server,
                "UNLIMITED_OCR_SUPPORTED_BACKENDS",
                {"transformers", "sglang"},
            ),
        ):
            await self.server.deploy_and_activate_model_runtime("unlimited-ocr", "sglang")
        ensure.assert_awaited_once_with("unlimited-ocr", "sglang")
        activate.assert_awaited_once_with("unlimited-ocr")
        save.assert_called_once_with({"unlimitedOcrBackend": "sglang"})

        with (
            patch.object(self.server, "unlimited_ocr_runtime_backend", "transformers"),
            patch.object(
                self.server,
                "ensure_model_runtime_created",
                new=AsyncMock(side_effect=RuntimeError("deploy failed")),
            ),
            patch.object(self.server, "save_runtime_settings") as rollback_save,
            patch.object(
                self.server,
                "UNLIMITED_OCR_SUPPORTED_BACKENDS",
                {"transformers", "sglang"},
            ),
            patch.object(
                self.server,
                "model_failure_diagnostics",
                new=AsyncMock(return_value={}),
            ),
            patch.object(self.server.logger, "exception"),
        ):
            await self.server.deploy_and_activate_model_runtime("unlimited-ocr", "sglang")
            self.assertEqual(self.server.unlimited_ocr_runtime_backend, "transformers")
        rollback_save.assert_called_once_with({"unlimitedOcrBackend": "transformers"})

        with (
            patch.object(
                self.server,
                "ensure_model_runtime_created",
                new=AsyncMock(side_effect=RuntimeError("deploy failed")),
            ),
            patch.object(self.server.logger, "exception"),
        ):
            await self.server.deploy_and_activate_model_runtime("pp-ocrv6")
        self.assertEqual(self.server.model_runtime_operation["state"], "error")

        with (
            patch.object(self.server, "ensure_model_runtime_created", new=AsyncMock()),
            patch.object(self.server, "activate_model_runtime", new=AsyncMock(return_value=False)),
            patch.object(
                self.server,
                "model_failure_diagnostics",
                new=AsyncMock(return_value={}),
            ),
            patch.object(self.server.logger, "exception"),
        ):
            await self.server.deploy_and_activate_model_runtime("pp-ocrv6")
        self.assertEqual(self.server.model_runtime_operation["state"], "error")

        with self.assertRaises(HTTPException) as unknown:
            await self.server.schedule_model_runtime_deploy("unknown")
        self.assertEqual(unknown.exception.status_code, 400)

        config = {"target": {}}
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(self.server, "model_control_available", return_value=False),
        ):
            with self.assertRaises(HTTPException) as unavailable:
                await self.server.schedule_model_runtime_deploy("target")
        self.assertEqual(unavailable.exception.status_code, 503)

        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(self.server, "ocr_active_count", 1),
        ):
            with self.assertRaises(HTTPException) as busy:
                await self.server.schedule_model_runtime_deploy("target")
        self.assertEqual(busy.exception.status_code, 409)

        self.server.model_runtime_task = FakeTask(done=False)
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(self.server, "model_control_available", return_value=True),
        ):
            with self.assertRaises(HTTPException) as switching:
                await self.server.schedule_model_runtime_deploy("target")
        self.assertEqual(switching.exception.status_code, 409)

        self.server.model_runtime_task = None
        self.server.unlimited_ocr_backend_task = FakeTask(done=False)
        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(self.server, "model_control_available", return_value=True),
        ):
            with self.assertRaises(HTTPException) as backend_busy:
                await self.server.schedule_model_runtime_deploy("target")
        self.assertEqual(backend_busy.exception.status_code, 409)

        self.server.unlimited_ocr_backend_task = None
        created = FakeTask()

        def create_task(coroutine):
            coroutine.close()
            return created

        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(self.server.asyncio, "create_task", side_effect=create_task),
        ):
            await self.server.schedule_model_runtime_deploy("target", "backend")
        self.assertIs(self.server.model_runtime_task, created)

    async def test_schedule_unlimited_backend_all_guards_and_noop(self):
        with patch.object(self.server, "ENABLE_UNLIMITED_OCR", False):
            with self.assertRaises(HTTPException) as disabled:
                await self.server.schedule_unlimited_ocr_backend_activation("sglang")
        self.assertEqual(disabled.exception.status_code, 404)

        with (
            patch.object(self.server, "ENABLE_UNLIMITED_OCR", True),
            patch.object(
                self.server,
                "UNLIMITED_OCR_SUPPORTED_BACKENDS",
                {"transformers", "sglang"},
            ),
            patch.object(self.server, "ocr_active_count", 1),
        ):
            with self.assertRaises(HTTPException) as ocr_busy:
                await self.server.schedule_unlimited_ocr_backend_activation("sglang")
        self.assertEqual(ocr_busy.exception.status_code, 409)

        self.server.model_runtime_task = FakeTask(done=False)
        with (
            patch.object(self.server, "ENABLE_UNLIMITED_OCR", True),
            patch.object(
                self.server,
                "UNLIMITED_OCR_SUPPORTED_BACKENDS",
                {"transformers", "sglang"},
            ),
        ):
            with self.assertRaises(HTTPException) as model_busy:
                await self.server.schedule_unlimited_ocr_backend_activation("sglang")
        self.assertEqual(model_busy.exception.status_code, 409)

        self.server.model_runtime_task = None
        self.server.unlimited_ocr_backend_task = FakeTask(done=False)
        with (
            patch.object(self.server, "ENABLE_UNLIMITED_OCR", True),
            patch.object(
                self.server,
                "UNLIMITED_OCR_SUPPORTED_BACKENDS",
                {"transformers", "sglang"},
            ),
        ):
            with self.assertRaises(HTTPException) as backend_busy:
                await self.server.schedule_unlimited_ocr_backend_activation("sglang")
        self.assertEqual(backend_busy.exception.status_code, 409)

        self.server.unlimited_ocr_backend_task = None
        with (
            patch.object(self.server, "ENABLE_UNLIMITED_OCR", True),
            patch.object(
                self.server,
                "UNLIMITED_OCR_SUPPORTED_BACKENDS",
                {"transformers", "sglang"},
            ),
            patch.object(self.server, "unlimited_ocr_runtime_backend", "transformers"),
            patch.object(self.server, "save_runtime_settings") as save,
        ):
            await self.server.schedule_unlimited_ocr_backend_activation("transformers")
        save.assert_called_once()

        created = FakeTask()

        def create_task(coroutine):
            coroutine.close()
            return created

        with (
            patch.object(self.server, "ENABLE_UNLIMITED_OCR", True),
            patch.object(
                self.server,
                "UNLIMITED_OCR_SUPPORTED_BACKENDS",
                {"transformers", "sglang"},
            ),
            patch.object(self.server, "unlimited_ocr_runtime_backend", "transformers"),
            patch.object(self.server.asyncio, "create_task", side_effect=create_task),
        ):
            await self.server.schedule_unlimited_ocr_backend_activation("sglang")
        self.assertIs(self.server.unlimited_ocr_backend_task, created)

    async def test_lifespan_initializes_store_and_schedules_when_controlled(self):
        with (
            patch.object(self.server, "ensure_task_data_dir") as ensure,
            patch.object(self.server, "model_control_available", return_value=False),
            patch.object(
                self.server,
                "schedule_model_runtime_activation",
                new=AsyncMock(),
            ) as schedule,
        ):
            async with self.server.lifespan(self.server.app):
                pass
        ensure.assert_called_once()
        schedule.assert_not_awaited()

        with (
            patch.object(self.server, "ensure_task_data_dir"),
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(
                self.server,
                "schedule_model_runtime_activation",
                new=AsyncMock(),
            ) as schedule,
        ):
            async with self.server.lifespan(self.server.app):
                pass
        schedule.assert_awaited_once_with(self.server.DEFAULT_RUNTIME_MODEL_ID)


if __name__ == "__main__":
    unittest.main()
