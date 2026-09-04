// Command connect-relay is the owner-run Grove Connect splice.
// Forwards framed blobs between two peers that share a session id.
// Does not decrypt. Chosen IPv4 only (never 0.0.0.0 / * / ::).
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

func main() {
	host := flag.String("host", connect.RelayDefaultHost, "Chosen IPv4 to bind (not 0.0.0.0 / * / ::)")
	port := flag.Int("port", connect.DefaultRelayPort, "TCP port (0 = ephemeral)")
	flag.Parse()

	os.Exit(run(*host, *port))
}

func run(host string, port int) int {
	relay, err := connect.StartRelay(host, port, 900*time.Second)
	if err != nil {
		fmt.Fprintf(os.Stderr, "connect-relay: %v\n", err)
		return 2
	}
	defer relay.Stop()

	fmt.Fprintf(
		os.Stderr,
		"connect-relay listening on %s (forwards framed blobs; does not decrypt)\n",
		relay.Addr(),
	)

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	<-ctx.Done()
	return 0
}
