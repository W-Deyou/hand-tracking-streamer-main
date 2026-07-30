using System;
using System.Net.Sockets;

public static class TcpConnectionHealth
{
    public static bool IsDisconnected(TcpClient client)
    {
        if (client == null || client.Client == null || !client.Connected)
        {
            return true;
        }

        try
        {
            Socket socket = client.Client;
            return socket.Poll(0, SelectMode.SelectRead) && socket.Available == 0;
        }
        catch (ObjectDisposedException)
        {
            return true;
        }
        catch (SocketException)
        {
            return true;
        }
    }
}
