// olisar-funnel: a tiny Tailscale Funnel sidecar.
//
// Joins the operator's tailnet using a pasted auth key (TS_AUTHKEY) and exposes
// Olisar's local dashboard to the public internet via Tailscale Funnel at a stable
// https://<hostname>.<tailnet>.ts.net URL — no domain required. All Funnel traffic is
// reverse-proxied to the local backend. Prints one machine-readable line so the parent
// process can react:
//
//	OLISAR_FUNNEL_URL=https://olisar.tailnet.ts.net   (on success)
//	OLISAR_FUNNEL_ERROR=<reason>                      (on failure)
package main

import (
	"context"
	"flag"
	"fmt"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"strings"
	"time"

	"tailscale.com/tsnet"
	"tailscale.com/util/dnsname"
)

func main() {
	hostname := flag.String("hostname", "olisar", "Tailscale node hostname")
	target := flag.String("target", "http://127.0.0.1:8723", "local backend URL to proxy to")
	stateDir := flag.String("state", "", "tsnet state directory (persists node identity across launches)")
	flag.Parse()

	srv := &tsnet.Server{
		Hostname: *hostname,
		Dir:      *stateDir,
		AuthKey:  os.Getenv("TS_AUTHKEY"),
		Logf:     func(string, ...any) {}, // quiet; we emit our own markers
	}
	defer srv.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	if _, err := srv.Up(ctx); err != nil {
		fail("couldn't join your tailnet: " + err.Error())
	}
	awaitName(srv, *hostname)

	ln, err := srv.ListenFunnel("tcp", ":443")
	if err != nil {
		// The most common cause is Funnel not being enabled for this node yet; the
		// error text from Tailscale includes the enable URL.
		fail(err.Error())
	}
	defer ln.Close()

	if u := publicURL(srv); u != "" {
		fmt.Printf("OLISAR_FUNNEL_URL=%s\n", u)
	}

	backend, err := url.Parse(*target)
	if err != nil {
		fail("bad target url: " + err.Error())
	}
	proxy := httputil.NewSingleHostReverseProxy(backend)
	// Forward the original public host + scheme so the backend's OAuth flow sees the
	// `.ts.net` origin (the cookie set on /auth/login then matches /auth/callback).
	defaultDirector := proxy.Director
	proxy.Director = func(r *http.Request) {
		origHost := r.Host
		defaultDirector(r) // rewrites r.URL + sets r.Host = backend host
		r.Header.Set("X-Forwarded-Host", origHost)
		r.Header.Set("X-Forwarded-Proto", "https") // Funnel terminates TLS for us
	}
	_ = http.Serve(ln, proxy) // blocks until the listener closes
}

// awaitName holds off until the node goes by the hostname it was started with. A renamed
// node can come up under its old name and take the new one a moment later, and Funnel is
// switched on for whichever name the node has when ListenFunnel asks, so asking too early
// would publish the old address. A node named by hand in Tailscale's admin console keeps
// that name whatever it's sent: that one gets a few seconds, then keeps its name.
func awaitName(srv *tsnet.Server, hostname string) {
	lc, err := srv.LocalClient()
	if err != nil {
		return
	}
	want := dnsname.SanitizeHostname(hostname)
	deadline := time.Now().Add(10 * time.Second)
	for {
		st, err := lc.StatusWithoutPeers(context.Background())
		if err == nil && st.Self != nil && hasName(st.Self.DNSName, want) {
			return
		}
		if time.Now().After(deadline) {
			return
		}
		time.Sleep(500 * time.Millisecond)
	}
}

// hasName reports whether a node's DNS name is the hostname it asked for, or that name with
// the -1, -2… Tailscale adds when another device in the tailnet already has it.
func hasName(dnsName, want string) bool {
	label := strings.ToLower(dnsname.FirstLabel(dnsName))
	if label == want {
		return true
	}
	n, ok := strings.CutPrefix(label, want+"-")
	if !ok || n == "" {
		return false
	}
	for _, r := range n {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

func publicURL(srv *tsnet.Server) string {
	lc, err := srv.LocalClient()
	if err != nil {
		return ""
	}
	st, err := lc.Status(context.Background())
	if err != nil || st.Self == nil {
		return ""
	}
	name := strings.TrimSuffix(st.Self.DNSName, ".")
	if name == "" {
		return ""
	}
	return "https://" + name
}

func fail(reason string) {
	fmt.Printf("OLISAR_FUNNEL_ERROR=%s\n", strings.ReplaceAll(strings.TrimSpace(reason), "\n", " "))
	os.Exit(1)
}
