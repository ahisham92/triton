using System.Collections.Generic;
using System.Drawing;
using System.Linq;
using System.Windows.Forms;

namespace Triton.Revit
{
    /// <summary>Which Triton views to draw, and where: each in its own drafting view, or into the active view.</summary>
    public class PickViewsForm : Form
    {
        readonly CheckedListBox list = new CheckedListBox { CheckOnClick = true, Dock = DockStyle.Fill, IntegralHeight = false };
        readonly ComboBox element = new ComboBox { DropDownStyle = ComboBoxStyle.DropDownList, Width = 260 };
        readonly RadioButton ownViews = new RadioButton { Text = "A drafting view for each (redrawn if it is already there)", AutoSize = true, Checked = true };
        readonly RadioButton activeView = new RadioButton { AutoSize = true };
        readonly TritonFile file;

        public List<DrawingView> Picked { get; private set; } = new List<DrawingView>();
        public bool IntoActiveView => activeView.Checked;

        public PickViewsForm(TritonFile file, string activeViewName)
        {
            this.file = file;
            Text = "Triton drawings: " + string.Join(", ", new[] { file.Project, file.Section }.Where(s => !string.IsNullOrWhiteSpace(s)));
            Size = new Size(560, 600);
            StartPosition = FormStartPosition.CenterScreen;
            MinimizeBox = MaximizeBox = false;

            var top = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, Padding = new Padding(8) };
            top.Controls.Add(new Label { Text = "Element", AutoSize = true, Padding = new Padding(0, 6, 0, 0) });
            element.Items.Add("All elements");
            foreach (var e in file.Views.Select(v => v.Element).Distinct()) element.Items.Add(e);
            element.SelectedIndex = 0;
            element.SelectedIndexChanged += (s, a) => Fill();
            top.Controls.Add(element);
            var all = new Button { Text = "All", AutoSize = true };
            all.Click += (s, a) => Tick(true);
            var none = new Button { Text = "None", AutoSize = true };
            none.Click += (s, a) => Tick(false);
            top.Controls.Add(all);
            top.Controls.Add(none);

            var bottom = new FlowLayoutPanel { Dock = DockStyle.Bottom, AutoSize = true, FlowDirection = FlowDirection.TopDown, Padding = new Padding(8) };
            bottom.Controls.Add(new Label { Text = "Draw into", AutoSize = true });
            bottom.Controls.Add(ownViews);
            activeView.Text = activeViewName == null
                ? "The active view (open a drafting, plan, section or detail view to use this)"
                : "The active view \"" + activeViewName + "\", at a point you click";
            activeView.Enabled = activeViewName != null;
            bottom.Controls.Add(activeView);
            var buttons = new FlowLayoutPanel { AutoSize = true, FlowDirection = FlowDirection.RightToLeft, Width = 520 };
            var ok = new Button { Text = "Draw", DialogResult = DialogResult.OK, AutoSize = true };
            var cancel = new Button { Text = "Cancel", DialogResult = DialogResult.Cancel, AutoSize = true };
            buttons.Controls.Add(cancel);
            buttons.Controls.Add(ok);
            bottom.Controls.Add(buttons);
            AcceptButton = ok;
            CancelButton = cancel;

            var middle = new Panel { Dock = DockStyle.Fill, Padding = new Padding(8, 0, 8, 0) };
            middle.Controls.Add(list);
            Controls.Add(middle);
            Controls.Add(top);
            Controls.Add(bottom);
            Fill();
            FormClosing += (s, a) =>
            {
                if (DialogResult == DialogResult.OK) Picked = list.CheckedItems.Cast<DrawingView>().ToList();
            };
        }

        void Fill()
        {
            list.Items.Clear();
            string pick = element.SelectedIndex > 0 ? (string)element.SelectedItem : null;
            foreach (var v in file.Views.Where(v => pick == null || v.Element == pick)) list.Items.Add(v, true);
        }

        void Tick(bool on)
        {
            for (int i = 0; i < list.Items.Count; i++) list.SetItemChecked(i, on);
        }
    }
}
