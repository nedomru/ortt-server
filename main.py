import asyncio
import websockets
import json
import logging
from aiohttp import web
import uuid
import aiohttp_cors
from dataclasses import dataclass, asdict
from typing import Dict, Optional
import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)


@dataclass
class Client:
    agreement_id: str
    city: str
    os: str
    hostname: str
    websocket: websockets.WebSocketServerProtocol
    last_seen: datetime.datetime


class DiagnosticServer:
    def __init__(self):
        self.clients: Dict[str, Client] = {}

    async def register_client(self, websocket: websockets.WebSocketServerProtocol, data: dict):
        """Register a new client"""
        client = Client(
            agreement_id=data['agreement_id'],
            city=data['city'],
            os=data['os'],
            hostname=data['hostname'],
            websocket=websocket,
            last_seen=datetime.datetime.now()
        )
        self.clients[websocket.id] = client
        logging.info(f"New client registered: {client.hostname} from {client.city}")

    async def unregister_client(self, websocket: websockets.WebSocketServerProtocol):
        """Remove a client when they disconnect"""
        if websocket.id in self.clients:
            client = self.clients.pop(websocket.id)
            logging.info(f"Client disconnected: {client.hostname}")

    async def handle_websocket(self, websocket: websockets.WebSocketServerProtocol):
        """Handle WebSocket connections"""
        try:
            async for message in websocket:
                try:
                    data = json.loads(message)
                    if data['type'] == 'registration':
                        await self.register_client(websocket, data['data'])
                    elif data['type'] == 'result':
                        # Store or forward diagnostic results
                        logging.info(f"Received {data['command']} results for {data['target']}")
                        logging.info(data)
                except json.JSONDecodeError:
                    logging.error("Received invalid JSON")
        except websockets.ConnectionClosed:
            logging.info("Client connection closed")
        finally:
            await self.unregister_client(websocket)

    async def send_command(self, client_id: uuid.UUID, command: str, target: str):
        """Send a diagnostic command to a specific client"""
        if client_id in self.clients:
            client = self.clients[client_id]
            message = {
                "type": "command",
                "command": command,
                "target": target
            }
            try:
                await client.websocket.send(json.dumps(message))
                logging.info(f"Sent {command} command to {client.hostname}")
                return {"status": "success", "message": f"Command sent to {client.hostname}"}
            except websockets.ConnectionClosed:
                await self.unregister_client(client.websocket)
                return {"status": "error", "message": "Client disconnected"}
        return {"status": "error", "message": "Client not found"}

    def get_clients(self):
        """Get list of connected clients, converting UUIDs to strings"""
        clients_list = []
        for client_id, client in self.clients.items():
            clients_list.append({
                "id": str(client_id),  # Convert UUID to string
                "agreement_id": client.agreement_id,
                "city": client.city,
                "os": client.os,
                "hostname": client.hostname,
                "last_seen": client.last_seen.isoformat()
            })
        return clients_list


async def handle_get_clients(request):
    """HTTP endpoint to get list of clients"""
    clients = request.app['server'].get_clients()
    return web.json_response(clients)


async def handle_send_command(request):
    """HTTP endpoint to send command to client"""
    try:
        data = await request.json()
        client_id_str = data.get('client_id') # Get the string representation
        command = data.get('command')
        target = data.get('target')

        if not all([client_id_str, command, target]): # Check for string representation
            return web.json_response(
                {"status": "error", "message": "Missing required parameters"},
                status=400
            )

        if command not in ['tracert', 'ping']:
            return web.json_response(
                {"status": "error", "message": "Invalid command"},
                status=400
            )
        
        try:
            client_id = uuid.UUID(client_id_str) # Parse back to UUID object
        except ValueError: # Handle invalid UUID strings
            return web.json_response(
                {"status": "error", "message": "Invalid client ID"},
                status=400
            )

        result = await request.app['server'].send_command(client_id, command, target)
        return web.json_response(result)
    except Exception as e:
        return web.json_response(
            {"status": "error", "message": str(e)},
            status=500
        )


async def start_server():
    # Create diagnostic server instance
    server = DiagnosticServer()

    # Create web application
    app = web.Application()
    app['server'] = server

    # Setup CORS
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*"
        )
    })

    # Add routes
    app.router.add_get('/api/clients', handle_get_clients)
    app.router.add_post('/api/send-command', handle_send_command)

    # Apply CORS to routes
    for route in list(app.router.routes()):
        cors.add(route)

    # Create WebSocket server
    ws_server = await websockets.serve(
        lambda websocket: server.handle_websocket(websocket),
        'localhost',
        8765
    )

    # Start HTTP server
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, 'localhost', 8080)

    # Start both servers
    await site.start()
    logging.info("HTTP server started on http://localhost:8080")
    logging.info("WebSocket server started on ws://localhost:8765")

    # Keep the server running
    await asyncio.Future()  # run forever


if __name__ == "__main__":
    asyncio.run(start_server())