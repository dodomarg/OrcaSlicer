#ifndef slic3r_GUI_ParameterPickerDialog_hpp_
#define slic3r_GUI_ParameterPickerDialog_hpp_

#include <string>
#include <vector>

#include <wx/dataview.h>
#include <wx/dialog.h>
#include <wx/srchctrl.h>

namespace Slic3r { namespace GUI {

struct ParameterPickerRow
{
    std::string key;
    std::string label;
    std::string category;
    std::string tooltip;
    // Serialized value as shown in the preset editor (vectors are ';'-separated).
    std::string value;
    bool        selected = false;
};

// Modal list of every parameter of one preset type: the user ticks the parameters to keep, edits
// their values, and filters by label, category or key. It reads the labels, categories and
// tooltips of the preset editor, so the names match what the Tab pages show.
class ParameterPickerDialog : public wxDialog
{
public:
    ParameterPickerDialog(wxWindow* parent, const wxString& title, std::vector<ParameterPickerRow> rows);

    // Rows the user ticked, with their edited values.
    std::vector<ParameterPickerRow> selected_rows() const;

private:
    void rebuild_view();
    void on_item_changed(wxDataViewEvent& evt);

    std::vector<ParameterPickerRow> m_rows;
    std::vector<size_t>             m_visible;
    wxSearchCtrl*                   m_search{nullptr};
    wxDataViewListCtrl*             m_list{nullptr};
    bool                            m_rebuilding{false};
};

}} // namespace Slic3r::GUI

#endif
