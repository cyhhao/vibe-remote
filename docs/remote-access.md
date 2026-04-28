# Remote Access

Vibe Remote can expose the local Web UI through a protected public URL by using Cloudflare Tunnel and Cloudflare Access.

The Cloudflare provider keeps the Vibe Remote UI bound to localhost. The public hostname should route through Cloudflare Tunnel to the local UI origin, and Cloudflare Access should protect the entire hostname before traffic reaches the origin.

## Cloudflare Provider

Use the Web UI `Remote Access` page to configure Cloudflare.

Required fields:

- Public hostname, such as `vibe-admin.example.com`
- Tunnel token copied from Cloudflare
- Safety confirmation that the Tunnel public hostname points only to the local UI origin
- Safety confirmation that Cloudflare Access protects the entire hostname

Optional fields:

- Cloudflare account ID
- Cloudflare zone ID
- Tunnel ID
- Access application ID
- Access AUD tag
- Allowed emails or email domains, used as operator notes for the intended Access policy

## Connector Lifecycle

Vibe Remote installs the `cloudflared` binary into its data directory when the user clicks `Install cloudflared`.

When the Cloudflare provider is enabled, Vibe Remote starts `cloudflared` in the background using the saved Tunnel token. The token is passed through the process environment instead of the command line so it is not visible in ordinary process command listings.

Vibe Remote stores the connector PID under its runtime directory and stops the connector when remote access is disabled or when the UI service is stopped. This avoids leaving orphaned tunnel processes after normal shutdowns.
