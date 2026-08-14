import asyncio
import importlib
import json
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
        self.request_response = request_response or response()
        self.get_response = get_response or response()
        self.post_response = post_response or response()
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
        self.server.set_model_runtime_operation("idle", "", self.server.DEFAULT_RUNTIME_MODEL_ID)

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
        with (
            patch.object(
                self.server,
                "wait_unlimited_ocr_adapter_http",
                new=AsyncMock(),
            ) as wait_http,
            patch.object(
                self.server,
                "call_unlimited_ocr_adapter_control",
                new=AsyncMock(),
            ) as control,
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(
                self.server,
                "ensure_runtime_service_created",
                new=AsyncMock(),
            ) as ensure,
            patch.object(
                self.server,
                "docker_container_action",
                new=AsyncMock(),
            ) as action,
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
        self.assertEqual(control.await_args.args[0], "/backend/transformers/unload")
        ensure.assert_awaited_once_with("unlimited-ocr-sglang")
        action.assert_awaited_once_with("unlimited-ocr-sglang", "start")
        wait_container.assert_awaited_once()
        wait_backend.assert_awaited_once_with("sglang", 100)

        with (
            patch.object(
                self.server,
                "wait_unlimited_ocr_adapter_http",
                new=AsyncMock(),
            ),
            patch.object(
                self.server,
                "call_unlimited_ocr_adapter_control",
                new=AsyncMock(),
            ) as control,
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(
                self.server,
                "docker_container_action",
                new=AsyncMock(),
            ) as action,
            patch.object(
                self.server,
                "wait_unlimited_ocr_backend_ready",
                new=AsyncMock(),
            ) as wait_backend,
        ):
            await self.server.ensure_unlimited_ocr_backend_runtime("transformers", 100)
        action.assert_awaited_once_with("unlimited-ocr-sglang", "stop")
        control.assert_awaited_once_with("/backend/transformers/preload", timeout=100)
        wait_backend.assert_awaited_once_with("transformers", 100)

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
            patch.object(self.server, "model_control_available", return_value=True),
            patch.object(
                self.server,
                "docker_container_action",
                new=AsyncMock(side_effect=RuntimeError("start failed")),
            ),
            patch.object(self.server.logger, "exception"),
        ):
            await self.server.activate_model_runtime("unlimited-ocr")
        self.assertEqual(self.server.model_runtime_operation["state"], "error")
        self.assertIn("start failed", self.server.model_runtime_operation["message"])

        with (
            patch.dict(self.server.MODEL_RUNTIME_CONFIG, config, clear=True),
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
        ):
            await self.server.activate_model_runtime("unlimited-ocr")
        ensure.assert_awaited_once()

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
            await self.server.schedule_model_runtime_activation("target")
        self.assertTrue(previous.cancelled)
        self.assertIs(self.server.model_runtime_task, created)

    async def test_deploy_and_schedule_model_runtime(self):
        with (
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
        save.assert_called_once()

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

        base_patches = (
            patch.object(self.server, "ENABLE_UNLIMITED_OCR", True),
            patch.object(
                self.server,
                "UNLIMITED_OCR_SUPPORTED_BACKENDS",
                {"transformers", "sglang"},
            ),
        )
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
