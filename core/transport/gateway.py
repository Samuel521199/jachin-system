"""
Jachin Link Gateway - gRPC Server
接收来自 Tier 3 的加密连接

职责：
- 监听 gRPC over HTTPS 连接
- 验证 Tier 3 的 mTLS 客户端证书（verify_mode=ssl.CERT_REQUIRED）
- 转发 AI 指令到 Jachin Brain
- 处理连接管理和心跳
"""

import asyncio
import logging
import ssl
import time
import json
from pathlib import Path
from typing import Optional, Dict, Set

import grpc
from grpc import aio
from cryptography.hazmat.primitives import serialization

from core.transport.mtls_manager import MTLSManager
from core.transport.connection_manager import ConnectionManager
from core.system.plugin_executor import PluginExecutor
from core.system.plugin_manager import PluginManager

# 导入 gRPC 代码（优先使用新协议）
# 注意：协议已迁移到 common/protocols/jachin_link.proto
logger = logging.getLogger(__name__)

# 尝试导入新协议（从 common/protocols/jachin_link.proto 生成）
try:
    from core.transport import jachin_link_pb2 as protocol_pb2
    from core.transport import jachin_link_pb2_grpc as protocol_pb2_grpc
    GRPC_CODE_AVAILABLE = True
    PROTOCOL_VERSION = "v3.2"
except ImportError:
    # 兼容旧版本导入（如果 gRPC 代码还未生成）
    try:
        from core.transport import protocol_pb2
        from core.transport import protocol_pb2_grpc
        GRPC_CODE_AVAILABLE = True
        PROTOCOL_VERSION = "archived"
        logger.warning("Using archived protocol_pb2. Please generate gRPC code from common/protocols/jachin_link.proto")
    except ImportError:
        GRPC_CODE_AVAILABLE = False
        PROTOCOL_VERSION = None
        logger.warning(
            "gRPC code not generated yet. Run:\n"
            "python -m grpc_tools.protoc -I common/protocols --python_out=core/transport "
            "--grpc_python_out=core/transport common/protocols/jachin_link.proto"
        )


class JachinLinkGatewayServicer:
    """
    Jachin Link Gateway gRPC 服务实现

    注意：实际的 gRPC 服务代码需要从 common/protocols/jachin_link.proto 生成
    这里提供接口定义和占位实现

    新协议支持：
    - InvokePlugin: 通用插件调用接口（Envelope Pattern）
    - StreamPlugin: 流式插件调用接口
    """

    def __init__(
        self,
        mtls_manager: MTLSManager,
        connection_manager: ConnectionManager,
        plugin_executor: Optional[PluginExecutor] = None
    ):
        """
        初始化服务处理器

        Args:
            mtls_manager: mTLS 证书管理器
            connection_manager: 连接管理器
            plugin_executor: 插件执行器（可选，如果为 None 则延迟初始化）
        """
        self.mtls_manager = mtls_manager
        self.connection_manager = connection_manager
        self.plugin_executor = plugin_executor

    def _extract_device_id_from_context(self, context) -> Optional[str]:
        """
        从 gRPC context 中提取设备 ID（从客户端证书）

        Args:
            context: gRPC 上下文

        Returns:
            设备 ID，如果无法提取则返回 None
        """
        try:
            # 从 peer 证书中提取设备 ID
            # gRPC 的 peer 证书信息在 context.peer() 和 context.auth_context() 中
            auth_context = context.auth_context()
            if auth_context:
                # 尝试从证书的 Common Name 或自定义扩展中提取设备 ID
                # 实际实现需要根据证书结构调整
                pass
        except Exception as e:
            logger.warning(f"Failed to extract device ID from context: {e}")
        return None

    async def Connect(self, request, context):
        """
        处理连接请求

        Args:
            request: ConnectRequest
            context: gRPC 上下文

        Returns:
            ConnectResponse
        """
        if not GRPC_CODE_AVAILABLE:
            raise RuntimeError("gRPC code not generated. Please run the protoc command first.")

        device_id = request.device_id

        # 从 context 中提取客户端证书并验证
        peer_device_id = self._extract_device_id_from_context(context)

        # 如果提供了客户端证书，验证它
        if request.client_certificate:
            try:
                verified, extracted_device_id = self.mtls_manager.verify_client_certificate(
                    request.client_certificate
                )
                if verified and extracted_device_id:
                    device_id = extracted_device_id
            except Exception as e:
                logger.error(f"Failed to verify client certificate: {e}")
                # 使用新的协议消息（如果可用）或兼容旧版本
                if GRPC_CODE_AVAILABLE:
                    try:
                        return jachin_link_pb2.ConnectResponse(
                            success=False,
                            message="Certificate verification failed",
                            requires_pairing=True,
                            error=str(e)
                        )
                    except AttributeError:
                        return protocol_pb2.ConnectResponse(
                            success=False,
                            message="Certificate verification failed",
                            requires_pairing=True,
                            error=str(e)
                        )
                else:
                    raise RuntimeError("gRPC code not available")

        logger.info(f"Connection request from device: {device_id}")

        # 检查是否需要配对
        requires_pairing = not self.connection_manager.is_device_connected(device_id)

        if requires_pairing:
            logger.info(f"Device '{device_id}' requires pairing")
            return protocol_pb2.ConnectResponse(
                success=False,
                server_id=self.mtls_manager.server_id,
                message="Device pairing required",
                requires_pairing=True
            )

        # 注册设备连接
        try:
            device_info = {}
            if request.device_info:
                device_info = json.loads(request.device_info)

            self.connection_manager.register_device(
                device_id=device_id,
                server_id=self.mtls_manager.server_id,
                metadata=device_info
            )

            logger.info(f"Device '{device_id}' connected successfully")
            return protocol_pb2.ConnectResponse(
                success=True,
                server_id=self.mtls_manager.server_id,
                message="Connected successfully",
                requires_pairing=False
            )
        except Exception as e:
            logger.error(f"Failed to register device '{device_id}': {e}")
            return protocol_pb2.ConnectResponse(
                success=False,
                message="Failed to register device",
                requires_pairing=False,
                error=str(e)
            )

    async def SendHeartbeat(self, request, context):
        """
        处理心跳请求

        Args:
            request: Heartbeat
            context: gRPC 上下文

        Returns:
            HeartbeatResponse
        """
        if not GRPC_CODE_AVAILABLE:
            raise RuntimeError("gRPC code not generated. Please run the protoc command first.")

        device_id = request.device_id

        # 更新心跳时间
        if self.connection_manager.update_heartbeat(device_id):
            logger.debug(f"Heartbeat from device: {device_id}")
            return protocol_pb2.HeartbeatResponse(
                success=True,
                message="OK",
                server_timestamp=int(time.time())
            )
        else:
            logger.warning(f"Heartbeat from unregistered device: {device_id}")
            return protocol_pb2.HeartbeatResponse(
                success=False,
                message="Device not registered",
                server_timestamp=int(time.time())
            )

    async def ExecuteCommandRequest(self, request, context):
        """
        处理命令执行请求

        Args:
            request: ExecuteCommand
            context: gRPC 上下文

        Returns:
            ExecuteCommandResponse
        """
        if not GRPC_CODE_AVAILABLE:
            raise RuntimeError("gRPC code not generated. Please run the protoc command first.")

        device_id = request.device_id
        command = request.command

        # 验证设备已连接
        if not self.connection_manager.is_device_connected(device_id):
            logger.warning(f"Command execution request from unregistered device: {device_id}")
            return protocol_pb2.ExecuteCommandResponse(
                request_id=request.request_id,
                success=False,
                error="Device not connected"
            )

        logger.info(f"Execute command '{command}' from device: {device_id}")

        # TODO: 转发到 Jachin Brain (Ray Actor)
        # - 调用 Ray Actor 执行命令
        # - 返回结果

        # 占位实现
        try:
            # 这里应该调用 Ray Actor 执行命令
            # result = await self._execute_via_ray_actor(command, request.parameters, request.payload)

            return protocol_pb2.ExecuteCommandResponse(
                request_id=request.request_id,
                success=True,
                result=f"Command '{command}' executed (placeholder)"
            )
        except Exception as e:
            logger.error(f"Failed to execute command '{command}': {e}")
            return protocol_pb2.ExecuteCommandResponse(
                request_id=request.request_id,
                success=False,
                error=str(e)
            )

    async def Pair(self, request, context):
        """
        处理配对请求（首次连接）

        Args:
            request: PairingRequest
            context: gRPC 上下文

        Returns:
            PairingResponse
        """
        if not GRPC_CODE_AVAILABLE:
            raise RuntimeError("gRPC code not generated. Please run the protoc command first.")

        server_id = request.server_id
        token = request.token
        csr_bytes = request.csr

        # TODO: 验证配对 token
        # if not self._verify_pairing_token(server_id, token):
        #     return protocol_pb2.PairingResponse(
        #         success=False,
        #         error="Invalid pairing token"
        #     )

        try:
            # 解析 CSR
            from cryptography import x509
            from cryptography.hazmat.backends import default_backend
            csr = x509.load_pem_x509_csr(csr_bytes, default_backend())

            # 从设备信息中提取设备 ID
            device_info = json.loads(request.device_info) if request.device_info else {}
            device_id = device_info.get('device_id', f"device-{int(time.time())}")

            # 签发客户端证书
            client_cert, client_cert_pem = self.mtls_manager.sign_client_csr(
                csr=csr,
                device_id=device_id
            )

            logger.info(f"Device '{device_id}' paired successfully")
            return protocol_pb2.PairingResponse(
                success=True,
                client_certificate=client_cert_pem
            )
        except Exception as e:
            logger.error(f"Failed to pair device: {e}")
            return protocol_pb2.PairingResponse(
                success=False,
                error=str(e)
            )

    async def InvokePlugin(self, request, context):
        """
        处理插件调用请求（通用信封模式）

        Args:
            request: PluginRequest
            context: gRPC 上下文

        Returns:
            PluginResponse
        """
        if not GRPC_CODE_AVAILABLE:
            raise RuntimeError("gRPC code not generated. Please run the protoc command first.")

        plugin_id = request.plugin_id
        method_name = request.method_name
        payload = request.payload
        trace_id = request.trace_id or f"trace-{int(time.time() * 1000)}"

        logger.info(f"InvokePlugin: plugin_id={plugin_id}, method={method_name}, trace_id={trace_id}")

        try:
            # 1. 确保插件执行器已初始化
            if not self.plugin_executor:
                # 延迟初始化（使用默认路径）
                plugins_dir = Path("data/plugins")
                skills_repo_dir = Path("skills_repo")
                plugin_manager = PluginManager(plugins_dir, skills_repo_dir)
                self.plugin_executor = PluginExecutor(plugin_manager)
                logger.info("PluginExecutor initialized with default paths")

            # 2. 调用插件执行器
            result = await self.plugin_executor.invoke_plugin(
                plugin_id=plugin_id,
                method_name=method_name,
                payload=payload,
                trace_id=trace_id
            )

            # 3. 构建 PluginResponse
            response = protocol_pb2.PluginResponse(
                status_code=result["status_code"],
                payload=result["payload"],
                trace_id=trace_id
            )

            # 设置错误信息（如果有）
            if result.get("error_message"):
                response.error_message = result["error_message"]

            # 设置 UI Schema（如果有）
            if result.get("ui_render_schema"):
                response.ui_render_schema = result["ui_render_schema"]

            logger.debug(f"Plugin '{plugin_id}' executed successfully, status={result['status_code']}")
            return response

        except Exception as e:
            logger.error(f"Failed to invoke plugin '{plugin_id}': {e}", exc_info=True)
            return protocol_pb2.PluginResponse(
                status_code=500,
                error_message=str(e),
                trace_id=trace_id
            )

    async def StreamPlugin(self, request, context):
        """
        处理流式插件调用请求（用于实时对话、流式输出）

        Args:
            request: PluginRequest
            context: gRPC 上下文

        Yields:
            PluginResponse (stream)
        """
        if not GRPC_CODE_AVAILABLE:
            raise RuntimeError("gRPC code not generated. Please run the protoc command first.")

        plugin_id = request.plugin_id
        method_name = request.method_name
        payload = request.payload
        trace_id = request.trace_id or f"trace-{int(time.time() * 1000)}"

        logger.info(f"StreamPlugin: plugin_id={plugin_id}, method={method_name}, trace_id={trace_id}")

        try:
            # 1. 确保插件执行器已初始化
            if not self.plugin_executor:
                plugins_dir = Path("data/plugins")
                skills_repo_dir = Path("skills_repo")
                plugin_manager = PluginManager(plugins_dir, skills_repo_dir)
                self.plugin_executor = PluginExecutor(plugin_manager)

            # 2. 获取插件 Actor（复用 invoke_plugin 的逻辑）
            manifest = self.plugin_executor.plugin_manager.get_plugin_manifest(plugin_id)
            if not manifest:
                yield protocol_pb2.PluginResponse(
                    status_code=404,
                    error_message=f"Plugin '{plugin_id}' not installed",
                    trace_id=trace_id
                )
                return

            actor_handle = await self.plugin_executor._get_or_create_plugin_actor(plugin_id, manifest)
            if not actor_handle:
                yield protocol_pb2.PluginResponse(
                    status_code=500,
                    error_message=f"Failed to initialize plugin '{plugin_id}'",
                    trace_id=trace_id
                )
                return

            # 3. 调用流式方法（约定：流式方法名以 _stream 结尾）
            stream_method_name = f"{method_name}_stream"
            if not hasattr(actor_handle, stream_method_name):
                # 如果没有流式方法，回退到普通方法并模拟流式输出
                logger.warning(f"Stream method '{stream_method_name}' not found, using regular method")
                result = await self.plugin_executor.invoke_plugin(
                    plugin_id=plugin_id,
                    method_name=method_name,
                    payload=payload,
                    trace_id=trace_id
                )
                yield protocol_pb2.PluginResponse(
                    status_code=result["status_code"],
                    payload=result["payload"],
                    ui_render_schema=result.get("ui_render_schema"),
                    error_message=result.get("error_message"),
                    trace_id=trace_id
                )
                return

            # 4. 调用流式方法并逐个返回结果
            try:
                payload_dict = json.loads(payload.decode('utf-8')) if payload else {}
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload_dict = {"raw": payload.hex()}

            stream_method = getattr(actor_handle, stream_method_name)
            result_refs = stream_method.remote(payload_dict, trace_id=trace_id)

            # 等待流式结果（支持 ObjectRefGenerator、ObjectRef 列表或单个 ObjectRef）
            async for chunk in self._stream_from_actor(result_refs, timeout=300.0):
                # 检查是否是错误响应
                if isinstance(chunk, dict) and "error" in chunk:
                    yield protocol_pb2.PluginResponse(
                        status_code=500 if "timeout" in chunk.get("error", "").lower() else 400,
                        error_message=chunk.get("error", "Unknown error"),
                        trace_id=trace_id
                    )
                    # 如果是超时错误，停止流式输出
                    if "timeout" in chunk.get("error", "").lower():
                        break
                else:
                    # 正常响应
                    yield protocol_pb2.PluginResponse(
                        status_code=200,
                        payload=json.dumps(chunk, ensure_ascii=False).encode('utf-8'),
                        trace_id=trace_id
                    )

        except Exception as e:
            logger.error(f"Failed to stream plugin '{plugin_id}': {e}", exc_info=True)
            yield protocol_pb2.PluginResponse(
                status_code=500,
                error_message=str(e),
                trace_id=trace_id
            )

    async def _stream_from_actor(self, result_refs, timeout: float = 300.0):
        """
        从 Ray Actor 流式获取结果

        支持多种返回类型：
        1. ObjectRefGenerator（Ray 生成器）- 推荐方式
        2. ObjectRef 列表 - 逐个获取
        3. 单个 ObjectRef - 等待完成后返回

        Args:
            result_refs: Ray ObjectRef、ObjectRefGenerator 或 ObjectRef 列表
            timeout: 流式超时时间（秒），默认 5 分钟

        Yields:
            结果块（字典格式）
        """
        import ray

        try:
            # 检查是否是 ObjectRefGenerator（Ray 生成器）
            if hasattr(result_refs, '__aiter__') or hasattr(result_refs, '__iter__'):
                # 这是一个生成器，使用异步迭代
                logger.debug("Detected ObjectRefGenerator, using async iteration")

                chunk_count = 0
                last_chunk_time = asyncio.get_event_loop().time()

                try:
                    # 支持异步迭代（推荐）
                    if hasattr(result_refs, '__aiter__'):
                        async for obj_ref in result_refs:
                            # 检查超时（每个 chunk 之间的最大间隔）
                            current_time = asyncio.get_event_loop().time()
                            if current_time - last_chunk_time > timeout:
                                logger.warning(f"Stream timeout: no chunk received for {timeout}s")
                                yield {"error": "Stream timeout", "chunk_count": chunk_count}
                                return

                            try:
                                # 等待 ObjectRef 完成（带超时）
                                # ray.get() 是同步阻塞的，需要在线程池中运行
                                chunk = await asyncio.wait_for(
                                    asyncio.to_thread(ray.get, obj_ref),
                                    timeout=30.0  # 单个 chunk 的超时
                                )
                                chunk_count += 1
                                last_chunk_time = current_time

                                # 如果 chunk 是字典，直接返回；否则包装
                                if isinstance(chunk, dict):
                                    yield chunk
                                else:
                                    yield {"data": chunk, "chunk_index": chunk_count}

                            except asyncio.TimeoutError:
                                logger.warning(f"Chunk {chunk_count + 1} timeout")
                                yield {"error": "Chunk timeout", "chunk_index": chunk_count}
                                break
                            except Exception as e:
                                logger.error(f"Error getting chunk {chunk_count + 1}: {e}")
                                yield {"error": str(e), "chunk_index": chunk_count}
                                break

                    # 同步迭代（备用）
                    else:
                        for obj_ref in result_refs:
                            try:
                                # ray.get() 是同步阻塞的，需要在线程池中运行
                                chunk = await asyncio.wait_for(
                                    asyncio.to_thread(ray.get, obj_ref),
                                    timeout=30.0
                                )
                                chunk_count += 1

                                if isinstance(chunk, dict):
                                    yield chunk
                                else:
                                    yield {"data": chunk, "chunk_index": chunk_count}

                            except Exception as e:
                                logger.error(f"Error getting chunk: {e}")
                                yield {"error": str(e), "chunk_index": chunk_count}
                                break

                except StopAsyncIteration:
                    logger.debug(f"Stream completed, total chunks: {chunk_count}")
                except Exception as e:
                    logger.error(f"Stream iteration error: {e}", exc_info=True)
                    yield {"error": f"Stream iteration failed: {str(e)}"}

            # 检查是否是 ObjectRef 列表
            elif isinstance(result_refs, list):
                logger.debug(f"Detected ObjectRef list with {len(result_refs)} items")

                for idx, obj_ref in enumerate(result_refs):
                    try:
                        # ray.get() 是同步阻塞的，需要在线程池中运行
                        chunk = await asyncio.wait_for(
                            asyncio.to_thread(ray.get, obj_ref),
                            timeout=30.0
                        )

                        if isinstance(chunk, dict):
                            yield chunk
                        else:
                            yield {"data": chunk, "chunk_index": idx}

                    except asyncio.TimeoutError:
                        logger.warning(f"Chunk {idx} timeout")
                        yield {"error": "Chunk timeout", "chunk_index": idx}
                        break
                    except Exception as e:
                        logger.error(f"Error getting chunk {idx}: {e}")
                        yield {"error": str(e), "chunk_index": idx}
                        break

            # 单个 ObjectRef（回退到非流式）
            else:
                logger.debug("Detected single ObjectRef, waiting for completion")
                try:
                    # ray.get() 是同步阻塞的，需要在线程池中运行
                    result = await asyncio.wait_for(
                        asyncio.to_thread(ray.get, result_refs),
                        timeout=timeout
                    )

                    # 如果结果是列表，逐个返回
                    if isinstance(result, list):
                        for idx, chunk in enumerate(result):
                            if isinstance(chunk, dict):
                                yield chunk
                            else:
                                yield {"data": chunk, "chunk_index": idx}
                    # 如果结果是字典，直接返回
                    elif isinstance(result, dict):
                        yield result
                    # 其他类型，包装后返回
                    else:
                        yield {"data": result}

                except asyncio.TimeoutError:
                    logger.error(f"Single ObjectRef timeout after {timeout}s")
                    yield {"error": "Stream timeout"}
                except Exception as e:
                    logger.error(f"Error getting single ObjectRef: {e}", exc_info=True)
                    yield {"error": str(e)}

        except Exception as e:
            logger.error(f"Unexpected error in stream processing: {e}", exc_info=True)
            yield {"error": f"Stream processing failed: {str(e)}"}


class JachinLinkGateway:
    """
    Jachin Link Gateway - 处理 Tier 3 连接
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 50051,
        cert_dir: Path = None,
        heartbeat_timeout: int = 60,
        plugin_manager: Optional[PluginManager] = None
    ):
        """
        初始化 Gateway

        Args:
            host: 监听地址
            port: 监听端口（gRPC over HTTPS）
            cert_dir: 证书存储目录
            heartbeat_timeout: 心跳超时时间（秒）
            plugin_manager: 插件管理器（可选，如果为 None 则使用默认路径）
        """
        self.host = host
        self.port = port
        self.server: Optional[aio.Server] = None

        # 初始化 mTLS 管理器
        if cert_dir is None:
            cert_dir = Path("data/certs")
        self.mtls_manager = MTLSManager(cert_dir)

        # 初始化连接管理器
        self.connection_manager = ConnectionManager(heartbeat_timeout=heartbeat_timeout)

        # 初始化插件执行器（如果提供了 plugin_manager）
        plugin_executor = None
        if plugin_manager:
            plugin_executor = PluginExecutor(plugin_manager)

        # 创建服务处理器
        self.servicer = JachinLinkGatewayServicer(
            self.mtls_manager,
            self.connection_manager,
            plugin_executor=plugin_executor
        )

    async def start(self):
        """启动 gRPC 服务器"""
        if not GRPC_CODE_AVAILABLE:
            raise RuntimeError(
                "gRPC code not generated. Please run:\n"
                "python -m grpc_tools.protoc -I common/protocols --python_out=core/transport "
                "--grpc_python_out=core/transport common/protocols/jachin_link.proto"
            )

        logger.info(f"Starting Jachin Link Gateway on {self.host}:{self.port}")

        # 1. 确保服务器证书存在
        cert, key = self.mtls_manager.load_server_certificate()
        if not cert or not key:
            logger.info("Generating server certificate...")
            cert, key = self.mtls_manager.generate_server_certificate()

        # 2. 加载 CA 证书（用于客户端验证）
        ca_cert, _ = self.mtls_manager.generate_ca()
        ca_cert_bytes = ca_cert.public_bytes(serialization.Encoding.PEM)

        # 3. 准备服务器证书和私钥（PEM 格式）
        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        key_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption()
        )

        # 4. 创建 gRPC Server Credentials（mTLS）
        server_credentials = grpc.ssl_server_credentials(
            [(key_pem, cert_pem)],
            root_certificates=ca_cert_bytes,
            require_client_auth=True  # 强制要求客户端证书
        )

        # 5. 创建并启动 gRPC Server
        self.server = aio.server()
        if not GRPC_CODE_AVAILABLE:
            raise RuntimeError(
                "gRPC code not generated. Please run:\n"
                "python -m grpc_tools.protoc -I common/protocols --python_out=core/transport "
                "--grpc_python_out=core/transport common/protocols/jachin_link.proto"
            )
        protocol_pb2_grpc.add_JachinLinkGatewayServicer_to_server(
            self.servicer, self.server
        )

        # 添加安全端口
        self.server.add_secure_port(f"{self.host}:{self.port}", server_credentials)
        await self.server.start()

        # 6. 启动连接清理任务
        await self.connection_manager.start_cleanup_task()

        logger.info(f"Jachin Link Gateway started on {self.host}:{self.port}")

    async def stop(self):
        """停止 gRPC 服务器"""
        # 停止连接清理任务
        await self.connection_manager.stop_cleanup_task()

        # 清理插件执行器
        if self.servicer.plugin_executor:
            await self.servicer.plugin_executor.cleanup()

        if self.server:
            await self.server.stop(grace=5)
            logger.info("Jachin Link Gateway stopped")

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()
