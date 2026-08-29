"""Socket connection to the Blender addon, plus addon-handshake state."""

import json
import logging
import os
import socket
import threading

from dataclasses import dataclass, field
from typing import Any

from ..addon_manager import AddonHandshake, format_handshake_log, handshake_addon

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("BlenderMCPServer")

# Default configuration
DEFAULT_HOST = "localhost"
DEFAULT_PORT = 9876

_addon_handshake = None
_addon_handshake_checked = False
_addon_handshake_lock = threading.Lock()


@dataclass
class BlenderConnection:
    host: str
    port: int
    sock: socket.socket = None  # Changed from 'socket' to 'sock' to avoid naming conflict
    # Serializes send+receive so two commands can never interleave on one socket.
    # Without this, a second command's response can be read as the first's, and
    # the stream stays desynced until the 180s timeout fires.
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def connect(self) -> bool:
        """
        Connect to the Blender addon socket server.

        Returns:
            bool: Result produced by the operation.

        """
        if self.sock:
            return True

        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.connect((self.host, self.port))
            logger.info(f"Connected to Blender at {self.host}:{self.port}")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to Blender: {e!s}")
            self.sock = None
            return False

    def disconnect(self) -> None:
        """Disconnect from the Blender addon."""
        if self.sock:
            try:
                self.sock.close()
            except Exception as e:
                logger.error(f"Error disconnecting from Blender: {e!s}")
            finally:
                self.sock = None

    def receive_full_response(self, sock, buffer_size=8192):
        """
        Receive the complete response, potentially in multiple chunks.

        Args:
            sock: Value for sock.
            buffer_size: Value for buffer size.

        Returns:
            Result produced by the operation.

        Raises:
            Exception: If the operation cannot be completed.

        """
        chunks = []
        # Use a consistent timeout value that matches the addon's timeout
        sock.settimeout(180.0)  # Match the addon's timeout

        try:
            while True:
                try:
                    chunk = sock.recv(buffer_size)
                    if not chunk:
                        # If we get an empty chunk, the connection might be closed
                        if not chunks:  # If we haven't received anything yet, this is an error
                            raise Exception("Connection closed before receiving any data")
                        break

                    chunks.append(chunk)

                    # Check if we've received a complete JSON object
                    try:
                        data = b"".join(chunks)
                        json.loads(data.decode("utf-8"))
                        # If we get here, it parsed successfully
                        logger.info(f"Received complete response ({len(data)} bytes)")
                        return data
                    except json.JSONDecodeError:
                        # Incomplete JSON, continue receiving
                        continue
                except TimeoutError:
                    # If we hit a timeout during receiving, break the loop and try to use what we have
                    logger.warning("Socket timeout during chunked receive")
                    break
                except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
                    logger.error(f"Socket connection error during receive: {e!s}")
                    raise  # Re-raise to be handled by the caller
        except TimeoutError:
            logger.warning("Socket timeout during chunked receive")
        except Exception as e:
            logger.error(f"Error during receive: {e!s}")
            raise

        # If we get here, we either timed out or broke out of the loop
        # Try to use what we have
        if chunks:
            data = b"".join(chunks)
            logger.info(f"Returning data after receive completion ({len(data)} bytes)")
            try:
                # Try to parse what we have
                json.loads(data.decode("utf-8"))
                return data
            except json.JSONDecodeError as exc:
                # If we can't parse it, it's incomplete
                raise Exception("Incomplete JSON response received") from exc
        else:
            raise Exception("No data received")

    def send_command(self, command_type: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Send a command to Blender and return the response.

        Args:
            command_type: Value for command type.
            params: Value for params.

        Returns:
            dict[str, Any]: Result produced by the operation.

        Raises:
            Exception: If the operation cannot be completed.

        """
        handshake = get_last_handshake()
        if handshake and handshake.capabilities and command_type not in handshake.capabilities:
            raise Exception(
                f"'{command_type}' is not supported by the installed Blender addon "
                f"(protocol {handshake.protocol_version}). Update the addon and reconnect."
            )
        # Hold the lock across send+receive: the response is matched to the
        # command purely by ordering on the stream, so overlapping calls would
        # hand each other's responses back.
        with self._lock:
            return self._send_command_locked(command_type, params)

    def _send_command_locked(self, command_type: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self.sock and not self.connect():
            raise ConnectionError("Not connected to Blender")

        command = {"type": command_type, "params": params or {}}

        try:
            # Log the command being sent
            logger.info(f"Sending command: {command_type} with params: {params}")

            # Send the command
            self.sock.sendall(json.dumps(command).encode("utf-8"))
            logger.info("Command sent, waiting for response...")

            # Set a timeout for receiving - use the same timeout as in receive_full_response
            self.sock.settimeout(180.0)  # Match the addon's timeout

            # Receive the response using the improved receive_full_response method
            response_data = self.receive_full_response(self.sock)
            logger.info(f"Received {len(response_data)} bytes of data")

            response = json.loads(response_data.decode("utf-8"))
            logger.info(f"Response parsed, status: {response.get('status', 'unknown')}")

            if response.get("status") == "error":
                logger.error(f"Blender error: {response.get('message')}")
                raise Exception(response.get("message", "Unknown error from Blender"))

            return response.get("result", {})
        except TimeoutError as exc:
            logger.error("Socket timeout while waiting for response from Blender")
            # Don't try to reconnect here - let the get_blender_connection handle reconnection
            # Just invalidate the current socket so it will be recreated next time
            self.sock = None
            raise Exception(
                "Timeout waiting for Blender response - try simplifying your request. If Blender is running headless (blender -b), commands never execute; run Blender with a GUI or via 'xvfb-run -a blender' instead"
            ) from exc
        except (ConnectionError, BrokenPipeError, ConnectionResetError) as e:
            logger.error(f"Socket connection error: {e!s}")
            self.sock = None
            raise Exception(f"Connection to Blender lost: {e!s}") from e
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from Blender: {e!s}")
            # Try to log what was received
            if "response_data" in locals() and response_data:
                logger.error(f"Raw response (first 200 bytes): {response_data[:200]}")
            raise Exception(f"Invalid response from Blender: {e!s}") from e
        except Exception as e:
            logger.error(f"Error communicating with Blender: {e!s}")
            # Don't try to reconnect here - let the get_blender_connection handle reconnection
            self.sock = None
            raise Exception(f"Communication error with Blender: {e!s}") from e


# Global connection for resources (since resources can't access context)
_blender_connection = None


def _maybe_handshake_addon(blender: BlenderConnection) -> None:
    """
    Run addon version handshake once per process after a live connection.

    Args:
        blender: Value for blender.

    """
    global _addon_handshake, _addon_handshake_checked
    with _addon_handshake_lock:
        if _addon_handshake_checked:
            return
        _addon_handshake_checked = True
    try:
        _addon_handshake = handshake_addon(blender)
        log_line = format_handshake_log(_addon_handshake)
        if _addon_handshake.up_to_date:
            logger.info(log_line)
        else:
            logger.warning(log_line)
    except Exception as e:
        logger.debug(f"Addon handshake skipped: {e}")


def get_blender_connection():
    """
    Get or create a persistent Blender connection.

    Returns:
        Result produced by the operation.

    Raises:
        Exception: If the operation cannot be completed.

    """
    global _blender_connection

    # Reuse the existing connection. We deliberately do NOT probe it with a
    # command here: that put two commands on the wire for every tool call, and
    # any overlap desynced the response stream until the socket timeout fired.
    # A dead socket is detected by the next real command and reconnected then.
    if _blender_connection is not None and _blender_connection.sock is not None:
        return _blender_connection

    # Create a new connection if needed
    if _blender_connection is None:
        host = os.getenv("BLENDER_HOST", DEFAULT_HOST)
        port = int(os.getenv("BLENDER_PORT", DEFAULT_PORT))
        _blender_connection = BlenderConnection(host=host, port=port)
        if not _blender_connection.connect():
            logger.error("Failed to connect to Blender")
            _blender_connection = None
            raise Exception("Could not connect to Blender. Make sure the Blender addon is running.")
        logger.info("Created new persistent connection to Blender")
        _maybe_handshake_addon(_blender_connection)

    return _blender_connection


def disconnect_blender() -> None:
    """
    Disconnect and clear the module-level Blender connection, if any.

    Lives here (not in app.py's server_lifespan) for the same reason
    force_addon_handshake does: reaching into another module's `global` via a
    plain import copies the reference, not a live binding, so clearing the
    connection on shutdown needs a real function in the module that owns it.
    """
    global _blender_connection
    if _blender_connection:
        logger.info("Disconnecting from Blender on shutdown")
        _blender_connection.disconnect()
        _blender_connection = None


def force_addon_handshake(blender: BlenderConnection) -> AddonHandshake | None:
    """
    Force a fresh addon handshake, bypassing the once-per-process cache.

    Does what `get_addon_status` used to do inline against this module's
    globals directly. That can't be replicated across a module boundary via a
    plain `from ..connection import _addon_handshake_checked` (it copies the
    reference, not a live binding), so this needs to be a real function here.

    Args:
        blender: Value for blender.

    Returns:
        AddonHandshake | None: Result produced by the operation.

    """
    global _addon_handshake_checked
    with _addon_handshake_lock:
        _addon_handshake_checked = False
    _maybe_handshake_addon(blender)
    return _addon_handshake


def get_last_handshake() -> AddonHandshake | None:
    """
    Read accessor for the most recent addon handshake result, if any.

    Returns:
        AddonHandshake | None: Result produced by the operation.

    """
    return _addon_handshake
