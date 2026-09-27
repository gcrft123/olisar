package main

import "testing"

func TestHasName(t *testing.T) {
	cases := []struct {
		dns, want string
		ok        bool
	}{
		{"olisar.tail1234.ts.net.", "olisar", true},
		{"olisar-2.tail1234.ts.net.", "olisar", true},    // another device already had it
		{"olisar-bot.tail1234.ts.net.", "olisar", false}, // a different name, not a suffix
		{"everest.tail1234.ts.net.", "olisar", false},    // the rename hasn't landed yet
		{"Everest.tail1234.ts.net.", "everest", true},
		{"olisar-.tail1234.ts.net.", "olisar", false},
		{"", "olisar", false},
	}
	for _, c := range cases {
		if got := hasName(c.dns, c.want); got != c.ok {
			t.Errorf("hasName(%q, %q) = %v, want %v", c.dns, c.want, got, c.ok)
		}
	}
}
