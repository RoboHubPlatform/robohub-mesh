package main

import (
	"crypto/ed25519"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"strings"
	"testing"
)

// The tests of the fwupdate port (MESH-0039, MESH-0040): a key's public half is
// its seed's, an offer is laid out as PROTO-0008 says and its signature verifies
// over "rh-update1" and the manifest, a bad argument is answered with "", and the
// header carries the key.

func TestAKeyAndItsPublicHalf(t *testing.T) {
	pair := fwupdateKeygen()
	seed, public, ok := strings.Cut(pair, "|")
	if !ok || len(seed) != 64 || len(public) != 64 {
		t.Fatalf("keygen = %q", pair)
	}
	if fwupdatePublic(seed) != public {
		t.Fatalf("the public half of the seed differs")
	}
	if fwupdatePublic("zz") != "" || fwupdatePublic("00") != "" {
		t.Fatalf("a bad seed has no public half")
	}
}

func TestAnOfferIsLaidOutAndSigned(t *testing.T) {
	seed, publicHex, _ := strings.Cut(fwupdateKeygen(), "|")
	image := strings.Repeat("\x00\x01image", 100)
	offerHex := fwupdateOffer(seed, "4096", "c0ffee0102030405", "7", "3", "192", image)
	offer, err := hex.DecodeString(offerHex)
	if err != nil || len(offer) != 122 {
		t.Fatalf("offer = %q (%d bytes)", offerHex, len(offer))
	}
	m := offer[1:58]
	if offer[0] != 1 || m[0] != 1 || binary.LittleEndian.Uint32(m[1:]) != 4096 ||
		hex.EncodeToString(m[5:13]) != "c0ffee0102030405" || binary.LittleEndian.Uint16(m[13:]) != 7 ||
		binary.LittleEndian.Uint32(m[15:]) != 3 || binary.LittleEndian.Uint32(m[19:]) != uint32(len(image)) ||
		binary.LittleEndian.Uint16(m[55:]) != 192 {
		t.Fatalf("the manifest is not laid out as PROTO-0008 says: %x", m)
	}
	digest := sha256.Sum256([]byte(image))
	if string(m[23:55]) != string(digest[:]) {
		t.Fatalf("the manifest's SHA-256 is not the image's")
	}
	public, _ := hex.DecodeString(publicHex)
	if !ed25519.Verify(public, append([]byte("rh-update1"), m...), offer[58:]) {
		t.Fatalf("the signature does not verify")
	}
	for _, bad := range [][]string{
		{"zz", "1", "c0ffee0102030405", "1", "1", "192", image},
		{seed, "x", "c0ffee0102030405", "1", "1", "192", image},
		{seed, "1", "c0ffee", "1", "1", "192", image},
		{seed, "1", "c0ffee0102030405", "70000", "1", "192", image},
		{seed, "1", "c0ffee0102030405", "1", "1", "0", image},
		{seed, "1", "c0ffee0102030405", "1", "1", "192", ""},
	} {
		if fwupdateOffer(bad[0], bad[1], bad[2], bad[3], bad[4], bad[5], bad[6]) != "" {
			t.Fatalf("a bad argument made an offer: %q", bad[:6])
		}
	}
}

func TestTheHeaderCarriesTheKey(t *testing.T) {
	_, public, _ := strings.Cut(fwupdateKeygen(), "|")
	header := fwupdateHeader(public)
	if !strings.Contains(header, "RH_UPDATE_KEY_PROVISIONED 1") ||
		!strings.Contains(header, "RH_UPDATE_PUBLIC_KEY[32] = { 0x"+public[0:2]+", ") {
		t.Fatalf("header = %q", header)
	}
	if fwupdateHeader("00") != "" {
		t.Fatalf("a bad key has no header")
	}
}
