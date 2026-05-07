import Testing
@testable import Scout

@Suite("WeekdaysFormatter")
struct WeekdaysFormatterTests {

    @Test("Mon-Fri yields 'weekdays'")
    func test_weekdays() {
        #expect(WeekdaysFormatter.label(for: ["Mon", "Tue", "Wed", "Thu", "Fri"]) == "weekdays")
    }

    @Test("Sat+Sun yields 'weekends'")
    func test_weekends() {
        #expect(WeekdaysFormatter.label(for: ["Sat", "Sun"]) == "weekends")
    }

    @Test("All 7 yields 'every day'")
    func test_every_day() {
        #expect(WeekdaysFormatter.label(for: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]) == "every day")
    }

    @Test("Contiguous range yields 'Mon-Wed' style")
    func test_contiguous_range() {
        #expect(WeekdaysFormatter.label(for: ["Mon", "Tue", "Wed"]) == "Mon-Wed")
        #expect(WeekdaysFormatter.label(for: ["Wed", "Thu", "Fri"]) == "Wed-Fri")
    }

    @Test("Non-contiguous yields comma-list")
    func test_non_contiguous() {
        #expect(WeekdaysFormatter.label(for: ["Mon", "Wed", "Fri"]) == "Mon, Wed, Fri")
    }

    @Test("Single day yields the day name")
    func test_single_day() {
        #expect(WeekdaysFormatter.label(for: ["Tue"]) == "Tue")
    }

    @Test("Empty input yields empty string")
    func test_empty() {
        #expect(WeekdaysFormatter.label(for: []) == "")
    }

    @Test("Order-independent — Sat before Mon still detects weekend pair")
    func test_order_independent() {
        #expect(WeekdaysFormatter.label(for: ["Sun", "Sat"]) == "weekends")
        #expect(WeekdaysFormatter.label(for: ["Fri", "Mon", "Wed", "Tue", "Thu"]) == "weekdays")
    }
}
