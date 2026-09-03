import unittest

from hwintern.filters import Classifier, FilterConfig
from hwintern.models import Job


def job(title, company="Acme", desc="", terms=None, category="", location="Austin, TX", sponsorship=""):
    return Job(source="test", company=company, title=title, url=f"https://x.test/{abs(hash(title))}",
               external_id=str(abs(hash(title))), description=desc, terms=terms or [], category=category,
               location=location, sponsorship=sponsorship, has_full_description=bool(desc))


class ClassifierTests(unittest.TestCase):
    def setUp(self):
        self.clf = Classifier(FilterConfig(exclude_companies=[r"\bUniversity\b"]))

    def accepted(self, j):
        return self.clf.classify(j).accepted

    def test_hardware_intern_with_target_term(self):
        v = self.clf.classify(job("Hardware Engineering Intern - Summer 2027"))
        self.assertTrue(v.accepted)
        self.assertIn("hardware", v.categories)
        self.assertEqual(v.terms, ["Summer 2027"])

    def test_other_term_rejected(self):
        self.assertFalse(self.accepted(job("Electrical Engineer Intern (Summer 2026)")))
        self.assertFalse(self.accepted(job("FPGA Intern - Fall 2026")))
        self.assertFalse(self.accepted(job("ASIC Design Intern - Spring 2027")))

    def test_bare_year(self):
        self.assertTrue(self.accepted(job("2027 Internship - RF Engineering")))
        self.assertFalse(self.accepted(job("2026 Internship - RF Engineering")))

    def test_year_range(self):
        self.assertTrue(self.accepted(job("Embedded Systems Co-op 2026-2027")))

    def test_season_pairs(self):
        self.assertTrue(self.accepted(job("Embedded Intern - Spring/Summer 2027")))
        self.assertFalse(self.accepted(job("Embedded Intern - Fall/Winter 2026")))

    def test_unknown_term_is_flagged_not_rejected(self):
        v = self.clf.classify(job("Embedded Firmware Intern"))
        self.assertTrue(v.accepted)
        self.assertIn("term-unknown", v.flags)

    def test_unknown_term_can_be_rejected(self):
        clf = Classifier(FilterConfig(accept_unknown_term=False))
        self.assertFalse(clf.classify(job("Embedded Firmware Intern")).accepted)

    def test_pure_software_rejected(self):
        self.assertFalse(self.accepted(job("Software Engineer Intern - Summer 2027")))
        self.assertFalse(self.accepted(job("Full Stack Developer Intern")))
        self.assertFalse(self.accepted(job("Machine Learning Intern")))

    def test_software_with_hardware_keyword_accepted(self):
        self.assertTrue(self.accepted(job("Software Engineer Intern - Embedded Systems")))
        self.assertTrue(self.accepted(job("Robotics Software Intern - Manipulation")))
        self.assertTrue(self.accepted(job("Software Developer Intern - Avionics Software")))

    def test_software_with_only_context_words_rejected(self):
        self.assertFalse(self.accepted(job("Software Engineer Intern - Vehicle Software - Summer 2027")))
        self.assertFalse(self.accepted(job("Software Engineer Intern - Aeronautics Systems")))
        self.assertFalse(self.accepted(job("Software Engineer Intern - DV Commodities")))

    def test_acronyms_are_case_sensitive(self):
        self.assertTrue(self.accepted(job("ASIC Intern - Summer 2027")))
        self.assertTrue(self.accepted(job("EE Intern")))
        self.assertFalse(self.accepted(job("Intern - help me ate pd")))

    def test_seniority_and_non_engineering_excluded(self):
        for t in ("Senior Hardware Engineer", "Hardware Engineering Manager", "Electrical Engineer",
                  "Sales Intern - Hardware Division", "Data Trading Analyst Intern", "Technical Consultant Intern"):
            self.assertFalse(self.accepted(job(t)), t)

    def test_not_internship_rejected(self):
        self.assertFalse(self.accepted(job("Hardware Engineer II")))

    def test_coop_and_student_worker(self):
        self.assertTrue(self.accepted(job("Electrical Engineering Co-Op (Summer 2027)")))
        self.assertTrue(self.accepted(job("Student Worker - Firmware Engineer")))

    def test_aggregator_terms_and_category(self):
        self.assertTrue(self.accepted(job("Silicon Engineer Intern/Co-op", terms=["Summer 2027"])))
        self.assertFalse(self.accepted(job("Silicon Engineer Intern/Co-op", terms=["Summer 2026"])))
        # feed says Hardware but the title is generic engineering -> trusted
        self.assertTrue(self.accepted(job("Engineering Intern", terms=["Summer 2027"], category="Hardware")))
        # feed says Hardware but the title is clearly software -> not trusted
        self.assertFalse(self.accepted(job("Software Engineer Intern", terms=["Summer 2027"], category="Hardware")))
        # title names our term even though the feed disagrees
        self.assertTrue(self.accepted(job("PCB Design Intern Summer 2027", terms=["Fall 2026"])))

    def test_description_rescues_ambiguous_title(self):
        desc = "You will design PCB layouts, bring up embedded firmware and debug analog circuits in the lab."
        v = self.clf.classify(job("Engineering Intern", desc=desc))
        self.assertTrue(v.accepted)
        self.assertTrue(any(c.startswith("desc:") for c in v.categories))

    def test_ambiguous_title_without_description_needs_details(self):
        v = self.clf.classify(job("Engineering Intern"))
        self.assertFalse(v.accepted)
        self.assertTrue(v.needs_description)

    def test_flags(self):
        desc = ("Applicants must be a U.S. citizen due to ITAR. Summer 2027. "
                "We are unable to offer visa sponsorship for this role. PhD students preferred.")
        v = self.clf.classify(job("RF Engineer Intern", desc=desc))
        self.assertTrue(v.accepted)
        self.assertIn("citizenship-required", v.flags)
        self.assertIn("no-sponsorship", v.flags)
        self.assertIn("phd", v.flags)

    def test_exclude_flags(self):
        clf = Classifier(FilterConfig(exclude_flags=["citizenship-required"]))
        v = clf.classify(job("RF Engineer Intern", desc="Must be a U.S. citizen. Summer 2027."))
        self.assertFalse(v.accepted)

    def test_location_filters(self):
        clf = Classifier(FilterConfig(location_exclude=[r"India|China"]))
        self.assertFalse(clf.classify(job("Hardware Intern Summer 2027", location="Bangalore, India")).accepted)
        clf = Classifier(FilterConfig(location_include=[r", [A-Z]{2}\b|Remote"]))
        self.assertTrue(clf.classify(job("Hardware Intern Summer 2027", location="Austin, TX")).accepted)
        self.assertFalse(clf.classify(job("Hardware Intern Summer 2027", location="Munich, Germany")).accepted)

    def test_sponsorship_label_exclusion(self):
        clf = Classifier(FilterConfig(exclude_sponsorship=["U.S. Citizenship is Required"]))
        self.assertFalse(clf.classify(job("Hardware Intern", terms=["Summer 2027"],
                                          sponsorship="U.S. Citizenship is Required")).accepted)

    def test_company_exclusion(self):
        self.assertFalse(self.accepted(job("Undergraduate Research Assistant - Materials", company="Some University",
                                           terms=["Summer 2027"], category="Hardware")))

    def test_also_accept_terms(self):
        clf = Classifier(FilterConfig(also_accept_terms=["Spring 2027"]))
        self.assertTrue(clf.classify(job("ASIC Design Intern - Spring 2027")).accepted)


if __name__ == "__main__":
    unittest.main()


class PersonalisedConfigTests(unittest.TestCase):
    """The settings shipped in config.yaml: sponsorship needed, undergrad, all terms, robotics focus."""

    def setUp(self):
        self.clf = Classifier(FilterConfig(
            accept_other_terms=True, reject_terms=["Spring 2026", "Summer 2026"], reject_years=[2023, 2024, 2025],
            priority_keywords=["robot", "avionic", "drone", r"\bUAV\b", "maritime", r"\bships?\b", "embedded"],
            exclude_flags=["citizenship-required", "phd-title"], exclude_sponsorship=["U.S. Citizenship is Required"]))

    def test_other_terms_kept_and_flagged(self):
        v = self.clf.classify(job("FPGA Intern - Fall 2027"))
        self.assertTrue(v.accepted)
        self.assertIn("other-term", v.flags)
        v = self.clf.classify(job("FPGA Intern - Summer 2027"))
        self.assertTrue(v.accepted)
        self.assertNotIn("other-term", v.flags)
        self.assertFalse(self.clf.classify(job("FPGA Intern - Summer 2026")).accepted)
        self.assertFalse(self.clf.classify(job("FPGA Intern - Fall 2025")).accepted)
        self.assertTrue(self.clf.classify(job("FPGA Intern - Fall 2026")).accepted)
        self.assertTrue(self.clf.classify(job("FPGA Co-op", terms=["Spring 2027"])).accepted)
        self.assertFalse(self.clf.classify(job("FPGA Co-op", terms=["Summer 2026"])).accepted)

    def test_green_card_and_us_person_language_excluded(self):
        for desc in ("Applicants must be U.S. citizens or lawful permanent residents.",
                     "Must be a green card holder.",
                     "This role requires U.S. person status as defined by ITAR.",
                     "Active Secret clearance required."):
            v = self.clf.classify(job("Robotics Intern Summer 2027", desc=desc))
            self.assertFalse(v.accepted, desc)
            self.assertIn("citizenship-required", v.flags)

    def test_no_sponsorship_is_kept_but_flagged(self):
        v = self.clf.classify(job("Robotics Intern Summer 2027", desc="We are unable to sponsor visas for this role."))
        self.assertTrue(v.accepted)
        self.assertIn("no-sponsorship", v.flags)

    def test_phd_title_excluded_but_phd_mention_kept(self):
        self.assertFalse(self.clf.classify(job("PhD Intern - Hardware Research")).accepted)
        self.assertFalse(self.clf.classify(job("Ph.D. Research Hardware Intern")).accepted)
        v = self.clf.classify(job("Hardware Intern", desc="Open to BS, MS and PhD students. Summer 2027."))
        self.assertTrue(v.accepted)
        self.assertNotIn("grad-only", v.flags)

    def test_grad_only_flag(self):
        v = self.clf.classify(job("Hardware Intern", desc="Currently pursuing a Master's degree in EE. Summer 2027."))
        self.assertTrue(v.accepted)
        self.assertIn("grad-only", v.flags)
        v = self.clf.classify(job("Hardware Intern", desc="Pursuing a Bachelor's or Master's degree. Summer 2027."))
        self.assertNotIn("grad-only", v.flags)

    def test_priority_flag_and_ordering(self):
        from hwintern.notify import sort_for_notification
        a = job("Robotics Engineer Intern - Summer 2027", company="Zeta")
        b = job("ASIC Design Intern - Summer 2027", company="Alpha")
        c = job("Embedded Intern - Fall 2027", company="Beta")
        for j in (a, b, c):
            v = self.clf.classify(j)
            j.flags, j.score, j.tier = v.flags, v.score, v.tier
        self.assertIn("priority", a.flags)
        self.assertNotIn("priority", b.flags)
        self.assertEqual([j.company for j in sort_for_notification([b, c, a])], ["Zeta", "Beta", "Alpha"])

    def test_season_only_feed_term_is_ignored(self):
        v = self.clf.classify(job("Embedded Intern", terms=["Summer"]))
        self.assertTrue(v.accepted)
        self.assertIn("term-unknown", v.flags)
        v = self.clf.classify(job("Embedded Intern - Summer 2027", terms=["Fall"]))
        self.assertEqual(v.terms, ["Summer 2027"])

    def test_maritime_and_aircraft_vocabulary(self):
        self.assertTrue(self.clf.classify(job("Naval Architecture Intern - Ship Systems 2027")).accepted)
        self.assertTrue(self.clf.classify(job("Aircraft Systems Engineering Intern")).accepted)
        self.assertTrue(self.clf.classify(job("UUV Autonomy Intern")).accepted)


class CountryAndTierTests(unittest.TestCase):
    def setUp(self):
        self.clf = Classifier(FilterConfig(countries_allow=["US", "CA", "GB", "IT", "FR", "CH", "DE", "ES"],
                                           accept_other_terms=True, priority_keywords=["robot", "embedded"]))

    def test_country_whitelist(self):
        ok = lambda loc: self.clf.classify(job("Hardware Intern Summer 2027", location=loc)).accepted
        for loc in ("Austin, TX", "Toronto, ON, Canada", "London, UK", "Munich, Germany", "Paris", "Zürich",
                    "Milan, Italy", "Madrid, Spain", "Bengaluru, India; San Jose, CA"):
            self.assertTrue(ok(loc), loc)
        for loc in ("Cairo, Egypt", "Bengaluru, India", "Tel Aviv, Israel", "Shanghai, China", "Amsterdam, Netherlands",
                    "Sydney, Australia", "Dublin, Ireland"):
            self.assertFalse(ok(loc), loc)

    def test_unknown_location_kept_and_flagged(self):
        v = self.clf.classify(job("Hardware Intern Summer 2027", location="Remote"))
        self.assertTrue(v.accepted)
        self.assertIn("location-unknown", v.flags)
        self.assertIn("remote", v.flags)
        clf = Classifier(FilterConfig(countries_allow=["US"], drop_unknown_location=True))
        self.assertFalse(clf.classify(job("Hardware Intern Summer 2027", location="Multiple Locations")).accepted)

    def test_tiers(self):
        target = self.clf.classify(job("Embedded Robotics Intern - Summer 2027", location="Boston, MA"))
        self.assertEqual(target.tier, "target")
        self.assertGreaterEqual(target.score, 75)
        match = self.clf.classify(job("ASIC Design Intern - Summer 2027", location="Austin, TX"))
        self.assertEqual(match.tier, "match")
        safety = self.clf.classify(job("Mechanical Engineering Intern - Fall 2027", location="Remote",
                                       desc="We are unable to sponsor visas."))
        self.assertEqual(safety.tier, "safety")
        self.assertLess(safety.score, 55)


class GeoTests(unittest.TestCase):
    def test_countries_for(self):
        from hwintern.geo import countries_for
        self.assertEqual(countries_for("Palo Alto, CA; Fremont, CA"), {"US"})
        self.assertEqual(countries_for("Cambridge, UK"), {"GB"})
        self.assertEqual(countries_for("Cambridge, MA"), {"US"})
        self.assertEqual(countries_for("Vancouver, BC"), {"CA"})
        self.assertEqual(countries_for("Munich, Germany; SF"), {"DE", "US"})
        self.assertEqual(countries_for("Geneva"), {"CH"})
        self.assertEqual(countries_for("Cairo, Egypt"), {"OTHER"})
        self.assertEqual(countries_for("Remote"), set())
        self.assertEqual(countries_for("Remote - US"), {"US"})
        self.assertEqual(countries_for("LA"), {"US"})
