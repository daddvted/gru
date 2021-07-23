package main

import (
	"crypto/tls"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/caarlos0/env"
	log "github.com/sirupsen/logrus"
	"github.com/ski2per/gru/minion"
)

var m = minion.Minion{}

func init() {
	// Init configuration from evnironmental variables
	// cfg := config{}
	if err := env.Parse(&m); err != nil {
		fmt.Printf("%+v\n", err)
	}

	if len(m.MinionID) <= 0 {
		hostname, err := os.Hostname()
		if err != nil {
			log.Error("Error while getting hostname")
			log.Errorln(err)
		}
		m.MinionID = hostname
	}

	// Init Logrus, default to INFO
	log.SetFormatter(&log.TextFormatter{
		FullTimestamp:   true,
		TimestampFormat: "2006-01-02 15:04:05.00000",
	})
	logLvl, err := log.ParseLevel(m.LogLevel)
	if err != nil {
		logLvl = log.InfoLevel
	}
	log.SetLevel(logLvl)
	// Skip TLS secure verify
	http.DefaultTransport.(*http.Transport).TLSClientConfig = &tls.Config{InsecureSkipVerify: true}
}

func main() {
	fmt.Printf("\nMinion: %s\n\n", minion.Version)

	log.Debugf("%+v\n", m)
	randomPort := -1
	// Use os.Singal channel to some cleanups
	ch := make(chan os.Signal)

	signal.Notify(ch, os.Interrupt, syscall.SIGTERM)
	go func() {
		<-ch
		log.Info("Got OS signal, Deregister...")

		if randomPort > 0 {
			m.Deregister(randomPort)
		}
		os.Exit(1)
	}()

	for {
		log.Info("Trying to get random reversed port from Gru")
		randomPort = m.GetRandomPort()
		log.Infof("Got random reversed port: %d\n", randomPort)

		internalIP := minion.GetLocalAddr()
		meta := minion.Meta{
			Name:       m.MinionID,
			Port:       randomPort,
			InternalIP: internalIP.String(),
		}

		err := m.Register(meta)
		if err != nil {
			time.Sleep(2 * time.Second)
			continue
		}

		err = minion.ConnectToGru(&m, randomPort)
		if err != nil {
			log.Error("Lost connection to Gru, try to reconnect...")
			time.Sleep(2 * time.Second)
			continue
		}
	}
}
